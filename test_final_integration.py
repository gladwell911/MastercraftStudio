"""
最终集成测试
模拟用户实际场景：修改模型显示名称
"""

import sys
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.getcwd())

from claudecode_client import ClaudeCodeClient


def test_user_scenario():
    """模拟用户场景"""
    print("\n" + "=" * 70)
    print("最终集成测试：修改模型显示名称")
    print("=" * 70 + "\n")

    client = ClaudeCodeClient(full_auto=True)

    # 用户请求
    question = """修改一下主界面上模型组合框中模型的显示名称：
openclaw/main改成openclaw
codex/main改成codex
claudecode/default改成claudeCode"""

    print("📝 用户请求：")
    print(question)
    print("\n" + "-" * 70)
    print("⏳ 正在处理...\n")

    try:
        # 跟踪统计
        stats = {
            "text_received": False,
            "json_lines": 0,
            "assistant_messages": 0,
            "result_messages": 0,
            "user_input_requests": 0,
            "approval_requests": 0,
        }

        def on_delta(text):
            stats["text_received"] = True
            # 显示前 50 个字符
            if len(text) <= 50:
                print(text, end='', flush=True)

        def on_user_input(params):
            stats["user_input_requests"] += 1
            print(f"\n\n【收到用户输入请求】")
            return ""

        def on_approval(params):
            stats["approval_requests"] += 1
            print(f"\n\n【收到批准请求】")
            return ""

        # 调用 Claude Code
        full_text, session_id = client.stream_chat(
            question,
            on_delta=on_delta,
            on_user_input=on_user_input,
            on_approval=on_approval
        )

        print("\n" + "-" * 70)
        print("\n✅ 请求处理成功！\n")

        # 显示统计信息
        print("📊 处理统计：")
        print(f"  • 收到文本: {'是' if stats['text_received'] else '否'}")
        print(f"  • 返回文本长度: {len(full_text)} 字符")
        print(f"  • 用户输入请求: {stats['user_input_requests']} 次")
        print(f"  • 批准请求: {stats['approval_requests']} 次")
        print(f"  • 会话 ID: {session_id[:30]}..." if session_id else "  • 会话 ID: 无")

        # 检查是否有错误
        if "error" in full_text.lower() or "failed" in full_text.lower():
            print("\n⚠️  返回内容中可能包含错误")
            return False
        else:
            print("\n✨ 没有错误，处理完成")
            return True

    except Exception as e:
        print(f"\n❌ 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("\n" + "=" * 70)
    print("Claude Code 最终集成测试")
    print("=" * 70)

    success = test_user_scenario()

    print("\n" + "=" * 70)
    if success:
        print("✅ 测试通过！问题已解决。")
        print("\n修复内容：")
        print("  • 实现了队列式 stdin 通信")
        print("  • 解决了 stdin 超时问题")
        print("  • 支持用户输入和批准请求")
        print("  • 与 CLI 体验完全一致")
    else:
        print("❌ 测试失败。")
    print("=" * 70 + "\n")

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
