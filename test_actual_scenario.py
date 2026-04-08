"""
Claude Code 实际场景测试
测试修改模型显示名称的完整流程
"""

import sys
import os
import time

# 设置 UTF-8 编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.getcwd())

from claudecode_client import ClaudeCodeClient


def test_model_display_name_modification():
    """测试修改模型显示名称"""
    print("\n" + "=" * 70)
    print("Claude Code 实际场景测试：修改模型显示名称")
    print("=" * 70 + "\n")

    client = ClaudeCodeClient(full_auto=True)

    # 用户请求
    question = """修改一下主界面上模型组合框中模型的显示名称：
openclaw/main改成openclaw
codex/main改成codex
claudecode/default改成claudeCode"""

    print(f"📝 发送请求：\n{question}\n")
    print("-" * 70)

    try:
        # 跟踪消息
        message_types = {}
        text_length = 0

        def on_delta(text):
            nonlocal text_length
            text_length += len(text)
            # 只打印前 100 个字符
            if text_length <= 100:
                print(text, end='', flush=True)
            elif text_length == len(text) + 100:
                print("\n... (内容过长，省略) ...", flush=True)

        def on_user_input(params):
            msg_type = "user_input"
            message_types[msg_type] = message_types.get(msg_type, 0) + 1
            print(f"\n\n【收到用户输入请求】")
            return ""

        def on_approval(params):
            msg_type = "approval"
            message_types[msg_type] = message_types.get(msg_type, 0) + 1
            print(f"\n\n【收到批准请求】")
            return ""

        # 调用 Claude Code
        print("\n⏳ 正在处理请求...\n")
        start_time = time.time()

        full_text, session_id = client.stream_chat(
            question,
            on_delta=on_delta,
            on_user_input=on_user_input,
            on_approval=on_approval
        )

        elapsed_time = time.time() - start_time

        print("\n" + "-" * 70)
        print(f"\n✅ 请求处理完成！")
        print(f"\n📊 统计信息：")
        print(f"  • 处理时间: {elapsed_time:.1f} 秒")
        print(f"  • 返回文本长度: {len(full_text)} 字符")
        print(f"  • 消息类型统计: {message_types}")
        print(f"  • 新会话 ID: {session_id[:20]}..." if session_id else "  • 新会话 ID: 无")

        # 检查是否有错误
        if "error" in full_text.lower() or "failed" in full_text.lower():
            print(f"\n⚠️  返回内容中可能包含错误信息")
        else:
            print(f"\n✨ 请求成功处理，没有错误")

        return True

    except Exception as e:
        print(f"\n❌ 请求失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_model_display_name_modification()
    sys.exit(0 if success else 1)
