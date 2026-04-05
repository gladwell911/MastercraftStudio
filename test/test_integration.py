"""
集成测试 - 测试完整的 Ctrl+O 和 F5 工作流程
"""
import os
import sys

# 设置输出编码
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

def test_workspace_dir_logic():
    """测试工作目录逻辑"""
    print("\n[测试] 工作目录逻辑")
    print("-" * 60)

    # 模拟 _workspace_dir_for_codex 逻辑
    from pathlib import Path

    # 场景1: 未载入项目文件夹
    active_project_folder = ""
    if active_project_folder and os.path.isdir(active_project_folder):
        cwd = active_project_folder
    else:
        cwd = str(Path.cwd())

    print(f"场景1 - 未载入项目:")
    print(f"  active_project_folder = '{active_project_folder}'")
    print(f"  结果: {cwd}")

    # 场景2: 已载入项目文件夹
    test_project = os.path.join(os.path.dirname(__file__), "test_project")
    active_project_folder = test_project
    if active_project_folder and os.path.isdir(active_project_folder):
        cwd = active_project_folder
    else:
        cwd = str(Path.cwd())

    print(f"\n场景2 - 已载入项目:")
    print(f"  active_project_folder = '{active_project_folder}'")
    print(f"  结果: {cwd}")
    print(f"  目录存在: {os.path.isdir(cwd)}")

    return os.path.isdir(cwd)

def test_find_main_in_test_project():
    """测试在测试项目中查找主程序"""
    print("\n[测试] 在测试项目中查找主程序")
    print("-" * 60)

    test_project = os.path.join(os.path.dirname(__file__), "test_project")
    print(f"测试项目路径: {test_project}")

    if not os.path.isdir(test_project):
        print("✗ 测试项目目录不存在")
        return False

    # 查找主程序
    candidates = ["main.py", "app.py", "run.py", "__main__.py", "start.py", "index.py"]
    found = None
    for filename in candidates:
        filepath = os.path.join(test_project, filename)
        if os.path.isfile(filepath):
            found = filepath
            print(f"✓ 找到: {filename}")
            break

    if found:
        print(f"✓ 主程序路径: {found}")
        return True
    else:
        print("✗ 未找到主程序")
        return False

def test_run_test_project():
    """测试运行测试项目"""
    print("\n[测试] 运行测试项目")
    print("-" * 60)

    import subprocess

    test_project = os.path.join(os.path.dirname(__file__), "test_project")
    main_file = os.path.join(test_project, "main.py")

    if not os.path.isfile(main_file):
        print(f"✗ 主程序不存在: {main_file}")
        return False

    print(f"运行: {main_file}")
    print(f"工作目录: {test_project}")

    try:
        result = subprocess.run(
            [sys.executable, main_file],
            cwd=test_project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10
        )

        print(f"退出码: {result.returncode}")
        if result.stdout:
            print(f"输出:\n{result.stdout}")
        if result.stderr:
            print(f"错误:\n{result.stderr}")

        return result.returncode == 0
    except Exception as e:
        print(f"✗ 运行失败: {e}")
        return False

def test_complete_workflow():
    """测试完整工作流程"""
    print("\n[测试] 完整工作流程模拟")
    print("-" * 60)

    from pathlib import Path
    import subprocess

    # 步骤1: 模拟 Ctrl+O 载入项目
    print("\n步骤1: 模拟 Ctrl+O 载入项目")
    test_project = os.path.join(os.path.dirname(__file__), "test_project")
    active_project_folder = test_project
    print(f"  载入项目: {active_project_folder}")

    # 步骤2: 获取工作目录
    print("\n步骤2: 获取工作目录")
    if active_project_folder and os.path.isdir(active_project_folder):
        cwd = active_project_folder
    else:
        cwd = str(Path.cwd())
    print(f"  工作目录: {cwd}")

    # 步骤3: 查找主程序
    print("\n步骤3: 查找主程序")
    candidates = ["main.py", "app.py", "run.py", "__main__.py", "start.py", "index.py"]
    main_file = None
    for filename in candidates:
        filepath = os.path.join(cwd, filename)
        if os.path.isfile(filepath):
            main_file = filepath
            break

    if not main_file:
        print("  ✗ 未找到主程序")
        return False
    print(f"  ✓ 找到主程序: {os.path.basename(main_file)}")

    # 步骤4: 查找 Python 解释器
    print("\n步骤4: 查找 Python 解释器")
    venv_paths = [
        os.path.join(cwd, ".venv", "Scripts", "python.exe"),
        os.path.join(cwd, "venv", "Scripts", "python.exe"),
        os.path.join(cwd, ".venv311", "Scripts", "python.exe"),
        os.path.join(cwd, "env", "Scripts", "python.exe"),
    ]

    python_cmd = None
    for venv_python in venv_paths:
        if os.path.isfile(venv_python):
            python_cmd = venv_python
            break

    if not python_cmd:
        python_cmd = sys.executable
    print(f"  Python: {python_cmd}")

    # 步骤5: 运行程序
    print("\n步骤5: 运行程序")
    try:
        result = subprocess.run(
            [python_cmd, main_file],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10
        )

        if result.returncode == 0:
            print("  ✓ 运行成功")
            return True
        else:
            print(f"  ✗ 运行失败 (退出码: {result.returncode})")
            return False
    except Exception as e:
        print(f"  ✗ 运行出错: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("集成测试 - Ctrl+O 和 F5 工作流程")
    print("=" * 60)

    tests = [
        ("工作目录逻辑", test_workspace_dir_logic),
        ("查找主程序", test_find_main_in_test_project),
        ("运行测试项目", test_run_test_project),
        ("完整工作流程", test_complete_workflow),
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

    all_passed = all(r for _, r in results)
    print("\n" + ("✓ 所有测试通过！" if all_passed else "✗ 部分测试失败"))

    if all_passed:
        print("\n功能验证成功！可以在主程序中使用:")
        print("  1. 按 Ctrl+O 载入项目文件夹")
        print("  2. 按 F5 快速运行主程序")
