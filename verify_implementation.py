#!/usr/bin/env python3
"""
Claude Code 实现验证脚本
验证所有组件是否正确实现
"""

import sys
import os
import json
import queue
import threading
import time

# 设置 UTF-8 编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


def verify_claudecode_client():
    """验证 claudecode_client.py 的实现"""
    print("\n=== 验证 claudecode_client.py ===")

    try:
        from claudecode_client import ClaudeCodeClient
        print("✓ 成功导入 ClaudeCodeClient")

        # 验证初始化
        client = ClaudeCodeClient(full_auto=True)
        assert hasattr(client, 'stdin_queue'), "应该有 stdin_queue 属性"
        assert isinstance(client.stdin_queue, queue.Queue), "stdin_queue 应该是 Queue 实例"
        print("✓ stdin_queue 已正确初始化")

        # 验证方法
        assert hasattr(client, 'send_user_input'), "应该有 send_user_input 方法"
        assert hasattr(client, '_stdin_writer'), "应该有 _stdin_writer 方法"
        assert hasattr(client, 'stream_chat'), "应该有 stream_chat 方法"
        print("✓ 所有必需的方法都存在")

        # 验证 send_user_input 功能
        client.send_user_input("test")
        msg = client.stdin_queue.get(timeout=1)
        assert msg == "test", "send_user_input 应该正确放入队列"
        print("✓ send_user_input 方法正常工作")

        return True
    except Exception as e:
        print(f"✗ 验证失败: {e}")
        return False


def verify_main_py_integration():
    """验证 main.py 的集成"""
    print("\n=== 验证 main.py 集成 ===")

    try:
        # 检查文件是否存在
        main_py_path = "C:\\code\\codex1\\main.py"
        if not os.path.exists(main_py_path):
            print(f"✗ 文件不存在: {main_py_path}")
            return False

        # 读取文件内容
        with open(main_py_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 验证关键代码片段
        checks = [
            ("_active_claudecode_client", "客户端跟踪"),
            ("self._active_claudecode_client = client", "客户端赋值"),
            ("self._active_claudecode_client.send_user_input", "消息拦截"),
            ("self._active_claudecode_client = None", "客户端清除"),
        ]

        for check_str, description in checks:
            if check_str in content:
                print(f"✓ 找到 {description}: {check_str[:50]}...")
            else:
                print(f"✗ 未找到 {description}: {check_str}")
                return False

        return True
    except Exception as e:
        print(f"✗ 验证失败: {e}")
        return False


def verify_tests():
    """验证测试文件"""
    print("\n=== 验证测试文件 ===")

    test_files = [
        ("C:\\code\\codex1\\test_claudecode_integration.py", "单元测试"),
        ("C:\\code\\codex1\\test_claudecode_e2e.py", "端到端测试"),
    ]

    all_exist = True
    for file_path, description in test_files:
        if os.path.exists(file_path):
            print(f"✓ {description}文件存在: {os.path.basename(file_path)}")
        else:
            print(f"✗ {description}文件不存在: {file_path}")
            all_exist = False

    return all_exist


def verify_documentation():
    """验证文档文件"""
    print("\n=== 验证文档文件 ===")

    doc_files = [
        ("C:\\code\\codex1\\CLAUDECODE_QUEUE_IMPLEMENTATION.md", "队列实现文档"),
        ("C:\\code\\codex1\\IMPLEMENTATION_SUMMARY.md", "实现总结"),
    ]

    all_exist = True
    for file_path, description in doc_files:
        if os.path.exists(file_path):
            print(f"✓ {description}存在: {os.path.basename(file_path)}")
        else:
            print(f"✗ {description}不存在: {file_path}")
            all_exist = False

    return all_exist


def run_quick_tests():
    """运行快速测试"""
    print("\n=== 运行快速测试 ===")

    try:
        from claudecode_client import ClaudeCodeClient
        from unittest.mock import MagicMock

        # 测试 1: 队列通信
        client = ClaudeCodeClient(full_auto=True)
        client.send_user_input("q1=1")
        msg = client.stdin_queue.get(timeout=1)
        assert msg == "q1=1"
        print("✓ 测试 1: 队列通信 - 通过")

        # 测试 2: stdin 写入线程
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_stdin = MagicMock()
        mock_proc.stdin = mock_stdin

        thread = threading.Thread(target=client._stdin_writer, args=(mock_proc,), daemon=True)
        thread.start()

        time.sleep(0.1)
        client.send_user_input("test")
        time.sleep(0.1)

        assert mock_stdin.write.called
        print("✓ 测试 2: stdin 写入线程 - 通过")

        # 停止线程
        mock_proc.poll.return_value = 0
        client.stdin_queue.put(None)
        thread.join(timeout=2)

        # 测试 3: 完全自动模式
        cmd = client._build_command("test", "")
        assert "--dangerously-skip-permissions" in cmd
        assert "--append-system-prompt" in cmd
        print("✓ 测试 3: 完全自动模式 - 通过")

        return True
    except Exception as e:
        print(f"✗ 快速测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主验证函数"""
    print("=" * 60)
    print("Claude Code 实现验证")
    print("=" * 60)

    results = []

    # 运行所有验证
    results.append(("claudecode_client.py", verify_claudecode_client()))
    results.append(("main.py 集成", verify_main_py_integration()))
    results.append(("测试文件", verify_tests()))
    results.append(("文档文件", verify_documentation()))
    results.append(("快速测试", run_quick_tests()))

    # 总结
    print("\n" + "=" * 60)
    print("验证总结")
    print("=" * 60)

    all_passed = True
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name}: {status}")
        if not result:
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("✅ 所有验证通过！实现完成。")
        return 0
    else:
        print("❌ 某些验证失败。请检查上面的错误信息。")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
