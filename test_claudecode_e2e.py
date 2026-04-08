"""
Claude Code 端到端集成测试
测试完整的交互流程
"""

import json
import queue
import threading
import time
import sys
from unittest.mock import Mock, patch, MagicMock, call
from claudecode_client import ClaudeCodeClient

# 设置 UTF-8 编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


def test_end_to_end_user_input_flow():
    """测试端到端用户输入流程"""
    print("\n=== 测试 1: 端到端用户输入流程 ===")

    client = ClaudeCodeClient(full_auto=True)

    # 模拟 Claude Code 的输出
    mock_stdout_lines = [
        # 首先返回一个 user_input 消息
        json.dumps({
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
        }),
        # 然后返回 assistant 消息
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "已选择选项1"}
                ]
            }
        }),
        # 最后返回 result 消息
        json.dumps({
            "type": "result",
            "session_id": "session-123",
            "result": "success"
        })
    ]

    # 模拟进程
    mock_proc = MagicMock()
    mock_proc.poll.side_effect = [None, None, None, 0]  # 前3次返回None，最后返回0
    mock_proc.returncode = 0
    mock_stdin = MagicMock()
    mock_proc.stdin = mock_stdin
    mock_proc.stdout = iter(mock_stdout_lines)
    mock_proc.stderr = iter([])

    # 跟踪回调调用
    user_input_called = False
    user_input_params = None

    def on_user_input(params: dict) -> str:
        nonlocal user_input_called, user_input_params
        user_input_called = True
        user_input_params = params
        # 模拟用户发送消息
        time.sleep(0.1)
        client.send_user_input("q1=1")
        return ""

    def on_delta(text):
        pass

    # 模拟 stream_chat 的部分逻辑
    full_text = ""
    new_session_id = ""

    # 启动 stdin 写入线程
    stdin_writer_thread = threading.Thread(target=client._stdin_writer, args=(mock_proc,), daemon=True)
    stdin_writer_thread.start()

    # 处理消息
    for raw_line in mock_stdout_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = str(obj.get("type") or "")

        if msg_type == "user_input":
            if callable(on_user_input):
                user_reply = on_user_input(obj)
                if user_reply:
                    client.send_user_input(user_reply)

        elif msg_type == "assistant":
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
            sid = str(obj.get("session_id") or "").strip()
            if sid:
                new_session_id = sid

    # 停止 stdin 写入线程
    client.stdin_queue.put(None)
    stdin_writer_thread.join(timeout=2)

    # 验证结果
    assert user_input_called, "用户输入回调应该被调用"
    assert user_input_params is not None, "用户输入参数应该被传递"
    assert user_input_params.get("type") == "user_input", "消息类型应该是 user_input"
    assert "已选择选项1" in full_text, "应该收到 assistant 消息"
    assert new_session_id == "session-123", "应该保存 session_id"
    print("✓ 端到端用户输入流程正常工作")


def test_end_to_end_approval_flow():
    """测试端到端批准流程"""
    print("\n=== 测试 2: 端到端批准流程 ===")

    client = ClaudeCodeClient(full_auto=True)

    # 模拟 Claude Code 的输出
    mock_stdout_lines = [
        # 首先返回一个 approval 消息
        json.dumps({
            "type": "approval",
            "description": "修改文件 main.py"
        }),
        # 然后返回 assistant 消息
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "已批准修改"}
                ]
            }
        }),
        # 最后返回 result 消息
        json.dumps({
            "type": "result",
            "session_id": "session-456",
            "result": "success"
        })
    ]

    # 模拟进程
    mock_proc = MagicMock()
    mock_proc.poll.side_effect = [None, None, None, 0]
    mock_proc.returncode = 0
    mock_stdin = MagicMock()
    mock_proc.stdin = mock_stdin
    mock_proc.stdout = iter(mock_stdout_lines)
    mock_proc.stderr = iter([])

    # 跟踪回调调用
    approval_called = False
    approval_params = None

    def on_approval(params: dict) -> str:
        nonlocal approval_called, approval_params
        approval_called = True
        approval_params = params
        # 模拟用户发送批准
        time.sleep(0.1)
        client.send_user_input("yes")
        return ""

    def on_delta(text):
        pass

    # 模拟 stream_chat 的部分逻辑
    full_text = ""
    new_session_id = ""

    # 启动 stdin 写入线程
    stdin_writer_thread = threading.Thread(target=client._stdin_writer, args=(mock_proc,), daemon=True)
    stdin_writer_thread.start()

    # 处理消息
    for raw_line in mock_stdout_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = str(obj.get("type") or "")

        if msg_type == "approval":
            if callable(on_approval):
                approval_reply = on_approval(obj)
                if approval_reply:
                    client.send_user_input(approval_reply)

        elif msg_type == "assistant":
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
            sid = str(obj.get("session_id") or "").strip()
            if sid:
                new_session_id = sid

    # 停止 stdin 写入线程
    client.stdin_queue.put(None)
    stdin_writer_thread.join(timeout=2)

    # 验证结果
    assert approval_called, "批准回调应该被调用"
    assert approval_params is not None, "批准参数应该被传递"
    assert approval_params.get("type") == "approval", "消息类型应该是 approval"
    assert "已批准修改" in full_text, "应该收到 assistant 消息"
    assert new_session_id == "session-456", "应该保存 session_id"
    print("✓ 端到端批准流程正常工作")


def test_stdin_queue_integration():
    """测试 stdin 队列集成"""
    print("\n=== 测试 3: stdin 队列集成 ===")

    client = ClaudeCodeClient(full_auto=True)

    # 模拟进程
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_stdin = MagicMock()
    mock_proc.stdin = mock_stdin

    # 启动 stdin 写入线程
    thread = threading.Thread(target=client._stdin_writer, args=(mock_proc,), daemon=True)
    thread.start()

    # 发送多个消息
    messages = ["q1=1", "q2=yes", "q3=自定义内容"]
    for msg in messages:
        client.send_user_input(msg)
        time.sleep(0.05)

    # 验证所有消息都被写入
    time.sleep(0.2)
    assert mock_stdin.write.call_count >= len(messages), "所有消息应该被写入 stdin"
    print(f"✓ 发送了 {len(messages)} 条消息，都被正确写入 stdin")

    # 停止线程
    mock_proc.poll.return_value = 0
    client.stdin_queue.put(None)
    thread.join(timeout=2)
    print("✓ stdin 队列集成正常工作")


def test_message_interception():
    """测试消息拦截机制"""
    print("\n=== 测试 4: 消息拦截机制 ===")

    # 模拟主程序的消息拦截
    class MockMainWindow:
        def __init__(self):
            self._active_claudecode_client = None

        def _submit_question(self, question: str) -> tuple[bool, str]:
            q = str(question or "").strip()
            if not q:
                return False, "请输入问题，输入框内容为空"

            # 检查是否有活跃的 Claude Code 客户端在等待输入
            if hasattr(self, '_active_claudecode_client') and self._active_claudecode_client is not None:
                # 将消息发送到 Claude Code 的 stdin 队列
                self._active_claudecode_client.send_user_input(q)
                return True, ""

            return False, "没有活跃的 Claude Code 客户端"

    window = MockMainWindow()
    client = ClaudeCodeClient(full_auto=True)

    # 设置活跃客户端
    window._active_claudecode_client = client

    # 提交问题
    ok, msg = window._submit_question("q1=1")
    assert ok, "消息应该被成功拦截"
    assert msg == "", "不应该有错误消息"

    # 验证消息被放入队列
    queued_msg = client.stdin_queue.get(timeout=1)
    assert queued_msg == "q1=1", "消息应该被放入队列"
    print("✓ 消息拦截机制正常工作")

    # 清除客户端
    window._active_claudecode_client = None
    ok, msg = window._submit_question("q2=2")
    assert not ok, "没有活跃客户端时应该返回失败"
    print("✓ 客户端清除机制正常工作")


def run_all_tests():
    """运行所有测试"""
    print("=" * 50)
    print("Claude Code 端到端集成测试")
    print("=" * 50)

    try:
        test_end_to_end_user_input_flow()
        test_end_to_end_approval_flow()
        test_stdin_queue_integration()
        test_message_interception()

        print("\n" + "=" * 50)
        print("✅ 所有端到端测试通过！")
        print("=" * 50)
        return True
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
