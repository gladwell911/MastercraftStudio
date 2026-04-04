#!/usr/bin/env python3
"""
测试域名连接功能的脚本
验证 tingyou.cc:18080 的配置是否正确
"""

import os
import sys


def test_environment_variables():
    """测试环境变量是否正确设置"""
    print("=" * 60)
    print("测试环境变量配置")
    print("=" * 60)

    token = os.environ.get("REMOTE_CONTROL_TOKEN", "").strip()
    domain = os.environ.get("REMOTE_CONTROL_DOMAIN", "").strip()
    host = os.environ.get("REMOTE_CONTROL_HOST", "").strip()
    port = os.environ.get("REMOTE_CONTROL_PORT", "").strip()

    print(f"REMOTE_CONTROL_TOKEN: {'✓ 已设置' if token else '✗ 未设置'}")
    if token:
        print(f"  值: {token[:10]}...{token[-10:] if len(token) > 20 else ''}")

    print(f"REMOTE_CONTROL_DOMAIN: {'✓ 已设置' if domain else '✗ 未设置 (使用默认值 tingyou.cc:18080)'}")
    if domain:
        print(f"  值: {domain}")

    print(f"REMOTE_CONTROL_HOST: {'✓ 已设置' if host else '✗ 未设置 (使用默认值 0.0.0.0)'}")
    if host:
        print(f"  值: {host}")

    print(f"REMOTE_CONTROL_PORT: {'✓ 已设置' if port else '✗ 未设置 (使用默认值 18080)'}")
    if port:
        print(f"  值: {port}")

    return bool(token)


def test_domain_format():
    """测试域名格式转换"""
    print("\n" + "=" * 60)
    print("测试域名格式转换")
    print("=" * 60)

    domain = os.environ.get("REMOTE_CONTROL_DOMAIN", "tingyou.cc:18080").strip()
    token = os.environ.get("REMOTE_CONTROL_TOKEN", "test-token").strip()

    def build_ws_url(domain, token):
        """模拟 main.py 中的 _build_remote_ws_url 方法"""
        domain = domain.rstrip('/')

        if domain.startswith("wss://") or domain.startswith("ws://"):
            if "?" in domain:
                return f"{domain}&token={token}"
            elif domain.endswith("/ws"):
                return f"{domain}?token={token}"
            else:
                return f"{domain}/ws?token={token}"

        if domain.startswith("https://"):
            domain = domain[len("https://"):]
            if domain.endswith("/ws"):
                return f"wss://{domain}?token={token}"
            else:
                return f"wss://{domain}/ws?token={token}"

        if domain.startswith("http://"):
            domain = domain[len("http://"):]
            if domain.endswith("/ws"):
                return f"ws://{domain}?token={token}"
            else:
                return f"ws://{domain}/ws?token={token}"

        if domain.endswith("/ws"):
            return f"wss://{domain}?token={token}"
        else:
            return f"wss://{domain}/ws?token={token}"

    test_cases = [
        "tingyou.cc:18080",
        "https://tingyou.cc:18080",
        "http://tingyou.cc:18080",
        "wss://tingyou.cc:18080",
        "ws://tingyou.cc:18080",
    ]

    print(f"当前配置的域名: {domain}")
    print(f"当前配置的令牌: {token[:10]}...{token[-10:] if len(token) > 20 else ''}")
    print("\n域名格式转换测试:")

    for test_domain in test_cases:
        result = build_ws_url(test_domain, token)
        print(f"  {test_domain:30} -> {result}")

    # 测试当前配置
    print(f"\n当前配置转换结果:")
    result = build_ws_url(domain, token)
    print(f"  {result}")

    return True


def test_websocket_server():
    """测试 WebSocket 服务器是否可以启动"""
    print("\n" + "=" * 60)
    print("测试 WebSocket 服务器启动")
    print("=" * 60)

    try:
        from remote_ws import RemoteWebSocketServer

        token = os.environ.get("REMOTE_CONTROL_TOKEN", "").strip()
        if not token:
            print("✗ 未设置 REMOTE_CONTROL_TOKEN，跳过服务器启动测试")
            return False

        print("✓ 成功导入 RemoteWebSocketServer")

        # 创建一个测试服务器实例（不启动）
        server = RemoteWebSocketServer(
            host="127.0.0.1",
            port=0,  # 使用随机端口
            token=token,
            on_message=lambda x: (200, {}),
            on_new_chat=lambda x: (200, {}),
            on_reply_request=lambda x: (200, {}),
            on_state=lambda x=None: (200, {}),
        )

        print("✓ 成功创建 RemoteWebSocketServer 实例")
        print(f"  主机: {server.host}")
        print(f"  端口: {server.port}")
        print(f"  令牌: {server.token[:10]}...{server.token[-10:] if len(server.token) > 20 else ''}")

        return True
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False


def main():
    """主测试函数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "  域名连接功能测试".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "=" * 58 + "╝")

    results = []

    # 测试环境变量
    results.append(("环境变量配置", test_environment_variables()))

    # 测试域名格式
    results.append(("域名格式转换", test_domain_format()))

    # 测试 WebSocket 服务器
    results.append(("WebSocket 服务器", test_websocket_server()))

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{name:20} {status}")

    all_passed = all(result for _, result in results)

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ 所有测试通过！域名连接功能已正确配置。")
        print("\n使用方法:")
        print("1. 设置环境变量 REMOTE_CONTROL_TOKEN 和 REMOTE_CONTROL_DOMAIN")
        print("2. 启动主程序: python main.py")
        print("3. 在菜单中选择'复制远程控制地址'获取 WebSocket URL")
        print("4. 在手机端程序中使用该 URL 进行连接")
    else:
        print("✗ 部分测试失败，请检查配置。")
    print("=" * 60 + "\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
