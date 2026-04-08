"""
Claude Code 实际交互测试
测试修改模型显示名称的请求
"""

import sys
import os

# 设置 UTF-8 编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# 添加当前目录到 Python 路径
sys.path.insert(0, os.getcwd())

from claudecode_client import ClaudeCodeClient


def test_real_interaction():
    """测试真实的 Claude Code 交互"""
    print("\n=== 测试真实的 Claude Code 交互 ===\n")

    client = ClaudeCodeClient(full_auto=True)

    # 用户请求
    question = """修改一下主界面上模型组合框中模型的显示名称：
openclaw/main改成openclaw
codex/main改成codex
claudecode/default改成claudeCode"""

    print(f"发送请求：\n{question}\n")
    print("=" * 60)

    try:
        # 跟踪消息
        message_count = 0
        user_input_count = 0
        approval_count = 0

        def on_delta(text):
            print(text, end='', flush=True)

        def on_user_input(params):
            nonlocal user_input_count
            user_input_count += 1
            print(f"\n\n【收到用户输入请求 #{user_input_count}】")
            print(f"参数: {params}\n")
            return ""

        def on_approval(params):
            nonlocal approval_count
            approval_count += 1
            print(f"\n\n【收到批准请求 #{approval_count}】")
            print(f"参数: {params}\n")
            return ""

        # 调用 Claude Code
        full_text, session_id = client.stream_chat(
            question,
            on_delta=on_delta,
            on_user_input=on_user_input,
            on_approval=on_approval
        )

        print("\n" + "=" * 60)
        print(f"\n✅ 交互成功完成！")
        print(f"收到用户输入请求: {user_input_count} 次")
        print(f"收到批准请求: {approval_count} 次")
        print(f"新会话 ID: {session_id}")
        print(f"\n完整回复长度: {len(full_text)} 字符")

        return True

    except Exception as e:
        print(f"\n❌ 交互失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_real_interaction()
    sys.exit(0 if success else 1)
