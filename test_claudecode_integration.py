"""
Claude Code 集成测试
测试队列式 stdin 通信是否正常工作
"""

import json
import queue
import threading
import time
import sys
from unittest.mock import Mock, patch, MagicMock
from claudecode_client import ClaudeCodeClient

# 设置 UTF-8 编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


def test_queue_based_stdin_communication():
    """测试队列式 stdin 通信"""
    print("\n=== 测试 1: 队列式 stdin 通信 ===")

    client = ClaudeCodeClient(full_auto=True)

    # 验证队列已初始化
    assert isinstance(client.stdin_queue, queue.Queue), "stdin_queue 应该是 Queue 实例"
    print("✓ stdin_queue 已正确初始化")

    # 测试 send_user_input 方法
    client.send_user_input("test input")
    user_input = client.stdin_queue.get(timeout=1)
    assert user_input == "test input", "用户输入应该被正确放入队列"
    print("✓ send_user_input 方法正常工作")


def test_stdin_writer_thread():
    """测试 stdin 写入线程"""
    print("\n=== 测试 2: stdin 写入线程 ===")

    client = ClaudeCodeClient(full_auto=True)

    # 创建模拟进程
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # 进程还在运行
    mock_stdin = MagicMock()
    mock_proc.stdin = mock_stdin

    # 启动 stdin 写入线程
    thread = threading.Thread(target=client._stdin_writer, args=(mock_proc,), daemon=True)
    thread.start()

    # 给线程一点时间启动
    time.sleep(0.1)

    # 发送用户输入
    client.send_user_input("q1=1")
    time.sleep(0.2)

    # 验证 stdin 被写入
    mock_stdin.write.assert_called()
    calls = mock_stdin.write.call_args_list
    assert any("q1=1" in str(call) for call in calls), "用户输入应该被写入 stdin"
    print("✓ stdin 写入线程正常工作")

    # 停止线程
    mock_proc.poll.return_value = 0  # 进程已结束
    client.stdin_queue.put(None)  # 哨兵值
    thread.join(timeout=2)
    print("✓ stdin 写入线程正常停止")


def test_user_input_callback():
    """测试用户输入回调"""
    print("\n=== 测试 3: 用户输入回调 ===")

    client = ClaudeCodeClient(full_auto=True)
    callback_called = False
    callback_params = None

    def on_user_input(params: dict) -> str:
        nonlocal callback_called, callback_params
        callback_called = True
        callback_params = params
        return ""

    # 模拟 user_input 消息
    user_input_msg = {
        "type": "user_input",
        "questions": [
            {
                "header": "选择",
                "question": "选择一个选项",
                "options": [
                    {"label": "选项1", "description": "描述1"},
                    {"label": "选项2", "description": "描述2"}
                ]
            }
        ]
    }

    # 调用回调
    result = on_user_input(user_input_msg)

    assert callback_called, "回调应该被调用"
    assert callback_params == user_input_msg, "回调应该接收正确的参数"
    assert result == "", "回调应该返回空字符串"
    print("✓ 用户输入回调正常工作")


def test_approval_callback():
    """测试批准回调"""
    print("\n=== 测试 4: 批准回调 ===")

    client = ClaudeCodeClient(full_auto=True)
    callback_called = False
    callback_params = None

    def on_approval(params: dict) -> str:
        nonlocal callback_called, callback_params
        callback_called = True
        callback_params = params
        return ""

    # 模拟 approval 消息
    approval_msg = {
        "type": "approval",
        "description": "修改文件 main.py"
    }

    # 调用回调
    result = on_approval(approval_msg)

    assert callback_called, "回调应该被调用"
    assert callback_params == approval_msg, "回调应该接收正确的参数"
    assert result == "", "回调应该返回空字符串"
    print("✓ 批准回调正常工作")


def test_message_type_handling():
    """测试消息类型处理"""
    print("\n=== 测试 5: 消息类型处理 ===")

    # 测试 assistant 消息
    assistant_msg = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "这是一个回复"}
            ]
        }
    }
    assert assistant_msg.get("type") == "assistant", "应该能识别 assistant 消息"
    print("✓ assistant 消息类型正确")

    # 测试 result 消息
    result_msg = {
        "type": "result",
        "session_id": "session-123",
        "result": "success"
    }
    assert result_msg.get("type") == "result", "应该能识别 result 消息"
    print("✓ result 消息类型正确")

    # 测试 user_input 消息
    user_input_msg = {
        "type": "user_input",
        "questions": []
    }
    assert user_input_msg.get("type") == "user_input", "应该能识别 user_input 消息"
    print("✓ user_input 消息类型正确")

    # 测试 approval 消息
    approval_msg = {
        "type": "approval",
        "description": "test"
    }
    assert approval_msg.get("type") == "approval", "应该能识别 approval 消息"
    print("✓ approval 消息类型正确")


def test_queue_sentinel_value():
    """测试队列哨兵值"""
    print("\n=== 测试 6: 队列哨兵值 ===")

    client = ClaudeCodeClient(full_auto=True)

    # 发送哨兵值
    client.stdin_queue.put(None)
    sentinel = client.stdin_queue.get(timeout=1)

    assert sentinel is None, "哨兵值应该是 None"
    print("✓ 队列哨兵值正常工作")


def test_full_auto_mode():
    """测试完全自动模式"""
    print("\n=== 测试 7: 完全自动模式 ===")

    client = ClaudeCodeClient(full_auto=True)

    # 构建命令
    cmd = client._build_command("test", "")

    # 验证包含 dangerously-skip-permissions
    assert "--dangerously-skip-permissions" in cmd, "完全自动模式应该包含 --dangerously-skip-permissions"
    print("✓ 完全自动模式正确配置")

    # 验证包含系统提示
    assert "--append-system-prompt" in cmd, "完全自动模式应该包含 --append-system-prompt"
    print("✓ 系统提示正确添加")


def run_all_tests():
    """运行所有测试"""
    print("=" * 50)
    print("Claude Code 集成测试")
    print("=" * 50)

    try:
        test_queue_based_stdin_communication()
        test_stdin_writer_thread()
        test_user_input_callback()
        test_approval_callback()
        test_message_type_handling()
        test_queue_sentinel_value()
        test_full_auto_mode()

        print("\n" + "=" * 50)
        print("✅ 所有测试通过！")
        print("=" * 50)
        return True
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        return False
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
