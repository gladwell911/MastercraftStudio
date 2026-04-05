"""
测试项目 - 用于测试 F5 快速运行功能
"""
import sys
import time

def main():
    print("=" * 60)
    print("测试项目启动成功！")
    print("=" * 60)
    print(f"Python 版本: {sys.version}")
    print(f"当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print("这是一个测试程序，用于验证 F5 快速运行功能。")
    print("如果你看到这条消息，说明程序运行成功！")
    print("=" * 60)
    return 0

if __name__ == "__main__":
    sys.exit(main())
