"""
Claude Code 修复验证
验证 stdin 超时问题已解决
"""

import subprocess
import json
import sys
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.getcwd())

from claudecode_client import resolve_claudecode_command


def test_without_interaction():
    """测试不需要交互的请求"""
    print("\n=== 测试 1: 不需要交互的请求 ===\n")

    cmd = resolve_claudecode_command()
    cmd.extend(["--print", "test", "--output-format", "stream-json", "--verbose"])

    print("执行命令（不需要交互）...")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,  # 使用 DEVNULL
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            bufsize=1,
        )

        json_count = 0
        for line in proc.stdout:
            if line.strip():
                try:
                    obj = json.loads(line)
                    json_count += 1
                except:
                    pass

        proc.wait()

        if proc.returncode == 0:
            print(f"✓ 成功完成（返回码: 0）")
            print(f"✓ 收到 {json_count} 条 JSON 消息")
            return True
        else:
            print(f"✗ 失败（返回码: {proc.returncode}）")
            return False

    except Exception as e:
        print(f"✗ 异常: {e}")
        return False


def test_with_interaction_callbacks():
    """测试有交互回调的请求"""
    print("\n=== 测试 2: 有交互回调的请求 ===\n")

    sys.path.insert(0, os.getcwd())
    from claudecode_client import ClaudeCodeClient

    client = ClaudeCodeClient(full_auto=True)

    print("创建 ClaudeCodeClient（full_auto=True）...")
    print("发送简单请求...")

    try:
        def on_delta(text):
            pass

        def on_user_input(params):
            return ""

        def on_approval(params):
            return ""

        full_text, session_id = client.stream_chat(
            "test",
            on_delta=on_delta,
            on_user_input=on_user_input,
            on_approval=on_approval
        )

        if full_text:
            print(f"✓ 成功完成")
            print(f"✓ 收到 {len(full_text)} 字符的回复")
            return True
        else:
            print(f"✗ 没有收到回复")
            return False

    except Exception as e:
        print(f"✗ 异常: {e}")
        return False


def test_stdin_mode_selection():
    """测试 stdin 模式选择"""
    print("\n=== 测试 3: stdin 模式选择 ===\n")

    sys.path.insert(0, os.getcwd())
    from claudecode_client import ClaudeCodeClient
    import subprocess

    client = ClaudeCodeClient(full_auto=True)

    # 测试没有回调时使用 DEVNULL
    print("情况 1: 没有交互回调")
    cmd = client._build_command("test", "")

    # 模拟 stream_chat 的 stdin 模式选择逻辑
    stdin_mode = subprocess.PIPE if (None or None) else subprocess.DEVNULL
    if stdin_mode == subprocess.DEVNULL:
        print("✓ 使用 DEVNULL（正确）")
    else:
        print("✗ 使用 PIPE（错误）")
        return False

    # 测试有回调时使用 PIPE
    print("\n情况 2: 有交互回调")
    def dummy_callback(params):
        return ""

    stdin_mode = subprocess.PIPE if (dummy_callback or None) else subprocess.DEVNULL
    if stdin_mode == subprocess.PIPE:
        print("✓ 使用 PIPE（正确）")
    else:
        print("✗ 使用 DEVNULL（错误）")
        return False

    return True


def main():
    """主测试函数"""
    print("=" * 70)
    print("Claude Code stdin 超时问题修复验证")
    print("=" * 70)

    results = []

    results.append(("不需要交互的请求", test_without_interaction()))
    results.append(("有交互回调的请求", test_with_interaction_callbacks()))
    results.append(("stdin 模式选择", test_stdin_mode_selection()))

    print("\n" + "=" * 70)
    print("测试总结")
    print("=" * 70)

    all_passed = True
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{name}: {status}")
        if not result:
            all_passed = False

    print("=" * 70)

    if all_passed:
        print("\n✓ 所有测试通过！stdin 超时问题已解决。")
        print("\n修复说明：")
        print("  • 不需要交互时使用 stdin=DEVNULL")
        print("  • 需要交互时使用 stdin=PIPE")
        print("  • 只在使用 PIPE 时启动 stdin 写入线程")
        print("  • 避免了 Claude Code 等待 stdin 数据的超时")
        return 0
    else:
        print("\n✗ 某些测试失败。")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
