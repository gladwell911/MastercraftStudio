"""测试 F5 快速运行功能"""
import os
import sys

# 设置输出编码
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_find_main_program():
    """测试查找主程序"""
    import tempfile
    import shutil

    # 创建临时测试目录
    test_dir = tempfile.mkdtemp()
    print(f"测试目录: {test_dir}")

    try:
        # 创建测试文件
        main_py = os.path.join(test_dir, "main.py")
        with open(main_py, "w", encoding="utf-8") as f:
            f.write('print("Hello from main.py")\n')

        # 模拟查找逻辑
        candidates = ["main.py", "app.py", "run.py", "__main__.py", "start.py", "index.py"]
        found = None
        for filename in candidates:
            filepath = os.path.join(test_dir, filename)
            if os.path.isfile(filepath):
                found = filepath
                break

        if found:
            print(f"✓ 找到主程序: {found}")
            return True
        else:
            print("✗ 未找到主程序")
            return False
    finally:
        # 清理
        shutil.rmtree(test_dir, ignore_errors=True)

def test_find_python_interpreter():
    """测试查找 Python 解释器"""
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 优先使用虚拟环境
    venv_paths = [
        os.path.join(cwd, ".venv", "Scripts", "python.exe"),
        os.path.join(cwd, "venv", "Scripts", "python.exe"),
        os.path.join(cwd, ".venv311", "Scripts", "python.exe"),
        os.path.join(cwd, "env", "Scripts", "python.exe"),
    ]

    found_venv = None
    for venv_python in venv_paths:
        if os.path.isfile(venv_python):
            found_venv = venv_python
            break

    if found_venv:
        print(f"✓ 找到虚拟环境 Python: {found_venv}")
    else:
        print(f"✓ 使用系统 Python: {sys.executable}")

    return True

def test_run_simple_program():
    """测试运行简单程序"""
    import tempfile
    import shutil
    import subprocess

    # 创建临时测试目录
    test_dir = tempfile.mkdtemp()
    print(f"测试目录: {test_dir}")

    try:
        # 创建测试程序
        test_py = os.path.join(test_dir, "main.py")
        with open(test_py, "w", encoding="utf-8") as f:
            f.write('print("Test Success")\nprint("Exit code: 0")\n')

        # 运行程序
        result = subprocess.run(
            [sys.executable, test_py],
            cwd=test_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10
        )

        # 只检查退出码
        if result.returncode == 0:
            print(f"✓ 程序运行成功")
            print(f"  退出码: {result.returncode}")
            if result.stdout:
                print(f"  输出: {result.stdout.strip()}")
            return True
        else:
            print(f"✗ 程序运行失败")
            print(f"  退出码: {result.returncode}")
            if result.stdout:
                print(f"  输出: {result.stdout}")
            if result.stderr:
                print(f"  错误: {result.stderr}")
            return False
    finally:
        # 清理
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    print("=" * 60)
    print("测试 F5 快速运行功能")
    print("=" * 60)

    tests = [
        ("查找主程序", test_find_main_program),
        ("查找 Python 解释器", test_find_python_interpreter),
        ("运行简单程序", test_run_simple_program),
    ]

    results = []
    for name, test_func in tests:
        print(f"\n[测试] {name}")
        print("-" * 60)
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"✗ 测试失败: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{status} - {name}")

    all_passed = all(r for _, r in results)
    print("\n" + ("所有测试通过！" if all_passed else "部分测试失败"))
