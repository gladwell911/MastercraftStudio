"""
测试 Claude Code 客户端的命令行参数
"""
import sys
import os

# 设置输出编码
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claudecode_client import ClaudeCodeClient

def test_command_building():
    """测试命令构建"""
    print("=" * 60)
    print("测试 Claude Code 客户端命令构建")
    print("=" * 60)

    # 测试 1: 默认模式（无自动批准）
    print("\n[测试 1] 默认模式")
    print("-" * 60)
    client = ClaudeCodeClient()
    cmd = client._build_command("测试问题", "")
    print("命令参数:")
    for i, arg in enumerate(cmd):
        print(f"  [{i}] {arg}")

    has_dangerous = "--dangerously-skip-permissions" in cmd
    has_bypass = "--permission-mode" in cmd and "bypassPermissions" in cmd
    print(f"\n包含 --dangerously-skip-permissions: {has_dangerous}")
    print(f"包含 --permission-mode bypassPermissions: {has_bypass}")

    # 测试 2: auto_approve 模式
    print("\n[测试 2] auto_approve 模式")
    print("-" * 60)
    client = ClaudeCodeClient(auto_approve=True)
    cmd = client._build_command("测试问题", "")
    print("命令参数:")
    for i, arg in enumerate(cmd):
        print(f"  [{i}] {arg}")

    has_dangerous = "--dangerously-skip-permissions" in cmd
    has_bypass = "--permission-mode" in cmd and "bypassPermissions" in cmd
    print(f"\n包含 --dangerously-skip-permissions: {has_dangerous}")
    print(f"包含 --permission-mode bypassPermissions: {has_bypass}")

    # 测试 3: full_auto 模式
    print("\n[测试 3] full_auto 模式（推荐）")
    print("-" * 60)
    client = ClaudeCodeClient(full_auto=True)
    cmd = client._build_command("测试问题", "")
    print("命令参数:")
    for i, arg in enumerate(cmd):
        print(f"  [{i}] {arg}")

    has_dangerous = "--dangerously-skip-permissions" in cmd
    has_system_prompt = "--append-system-prompt" in cmd
    print(f"\n包含 --dangerously-skip-permissions: {has_dangerous}")
    print(f"包含 --append-system-prompt: {has_system_prompt}")

    # 测试 4: 带 session_id
    print("\n[测试 4] 带 session_id 的 full_auto 模式")
    print("-" * 60)
    client = ClaudeCodeClient(full_auto=True)
    cmd = client._build_command("测试问题", "test-session-123")
    print("命令参数:")
    for i, arg in enumerate(cmd):
        print(f"  [{i}] {arg}")

    has_resume = "--resume" in cmd
    has_dangerous = "--dangerously-skip-permissions" in cmd
    print(f"\n包含 --resume: {has_resume}")
    print(f"包含 --dangerously-skip-permissions: {has_dangerous}")

    # 验证结果
    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)

    # 验证 full_auto 模式是否正确
    client = ClaudeCodeClient(full_auto=True)
    cmd = client._build_command("测试", "")

    checks = [
        ("包含 --dangerously-skip-permissions", "--dangerously-skip-permissions" in cmd),
        ("包含 --append-system-prompt", "--append-system-prompt" in cmd),
        ("不包含 --permission-mode", "--permission-mode" not in cmd),
    ]

    all_passed = True
    for check_name, result in checks:
        status = "✓" if result else "✗"
        print(f"{status} {check_name}")
        if not result:
            all_passed = False

    if all_passed:
        print("\n✓ 所有检查通过！")
        print("\n配置说明:")
        print("  - 使用 ClaudeCodeClient(full_auto=True) 创建客户端")
        print("  - 将使用 --dangerously-skip-permissions 跳过所有权限确认")
        print("  - 将添加系统提示指导 Claude 自主决策")
    else:
        print("\n✗ 部分检查失败")

    return all_passed

if __name__ == "__main__":
    test_command_building()
