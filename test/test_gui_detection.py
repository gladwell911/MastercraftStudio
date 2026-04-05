"""
测试 GUI 程序检测和运行
"""
import sys
import os

# 设置输出编码
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

def test_is_gui_program():
    """测试 GUI 程序检测"""
    print("\n[测试] GUI 程序检测")
    print("-" * 60)

    def is_gui_program(main_file: str) -> bool:
        """检测是否是 GUI 程序"""
        try:
            with open(main_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(5000)

                gui_indicators = [
                    'import wx',
                    'from wx',
                    'import tkinter',
                    'from tkinter',
                    'import PyQt5',
                    'from PyQt5',
                    'import PyQt6',
                    'from PyQt6',
                    'import PySide',
                    'from PySide',
                    'import kivy',
                    'from kivy',
                ]

                for indicator in gui_indicators:
                    if indicator in content:
                        return True

                return False
        except Exception:
            return False

    # 测试 windowsZhuge 项目
    zhuge_main = r"C:\code\windowsZhuge\main.py"
    if os.path.isfile(zhuge_main):
        is_gui = is_gui_program(zhuge_main)
        print(f"windowsZhuge/main.py: {'GUI 程序' if is_gui else '控制台程序'}")
    else:
        print(f"文件不存在: {zhuge_main}")

    # 测试 codex 项目
    codex_main = r"C:\code\codex\main.py"
    if os.path.isfile(codex_main):
        is_gui = is_gui_program(codex_main)
        print(f"codex/main.py: {'GUI 程序' if is_gui else '控制台程序'}")
    else:
        print(f"文件不存在: {codex_main}")

    # 测试测试项目
    test_main = r"C:\code\codex\test\test_project\main.py"
    if os.path.isfile(test_main):
        is_gui = is_gui_program(test_main)
        print(f"test_project/main.py: {'GUI 程序' if is_gui else '控制台程序'}")
    else:
        print(f"文件不存在: {test_main}")

    return True

def test_subprocess_with_new_console():
    """测试在新控制台窗口中运行程序"""
    print("\n[测试] 在新窗口中运行程序")
    print("-" * 60)

    import subprocess
    import tempfile
    import time

    # 创建测试脚本
    test_dir = tempfile.mkdtemp()
    test_script = os.path.join(test_dir, "test_gui.py")

    with open(test_script, 'w', encoding='utf-8') as f:
        f.write("""
import time
print("测试程序启动")
print("这个窗口将在 3 秒后自动关闭")
for i in range(3, 0, -1):
    print(f"{i}...")
    time.sleep(1)
print("程序结束")
""")

    print(f"测试脚本: {test_script}")

    try:
        if os.name == 'nt':
            CREATE_NEW_CONSOLE = 0x00000010
            proc = subprocess.Popen(
                [sys.executable, test_script],
                cwd=test_dir,
                creationflags=CREATE_NEW_CONSOLE,
                shell=False
            )
            print(f"✓ 程序已在新窗口中启动 (PID: {proc.pid})")
            print("  请检查是否弹出了新的控制台窗口")
            return True
        else:
            print("✗ 此测试仅支持 Windows")
            return False
    except Exception as e:
        print(f"✗ 启动失败: {e}")
        return False
    finally:
        # 清理
        import shutil
        time.sleep(1)
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    print("=" * 60)
    print("测试 GUI 程序检测和运行")
    print("=" * 60)

    tests = [
        ("GUI 程序检测", test_is_gui_program),
        ("新窗口运行", test_subprocess_with_new_console),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ 测试异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{status} - {name}")
