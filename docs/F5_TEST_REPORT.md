# F5 快速运行功能 - 测试报告

## 测试日期
2026-04-01

## 修复内容

### 问题 1: F5 只能在输入框中使用
**修复**: 将 F5 和 Ctrl+O 快捷键绑定移到 `_on_char_hook` 方法中，实现全局快捷键。

**修改位置**: `main.py` 第 2703 行

**效果**: 现在可以在窗口的任意位置按 F5 运行程序，按 Ctrl+O 载入项目。

### 问题 2: GUI 程序窗口不显示
**修复**: 
1. 添加 GUI 程序检测功能 (`_is_gui_program` 方法)
2. 对于 GUI 程序，使用 `CREATE_NEW_CONSOLE` 标志启动新窗口
3. 对于控制台程序，继续捕获输出

**修改位置**: `main.py` 第 3078 行

**效果**: GUI 程序会在新窗口中显示，控制台程序的输出会被捕获并显示。

## 测试结果

### 1. GUI 程序检测测试
✓ **通过**

测试文件:
- `C:\code\windowsZhuge\main.py` → 检测为 GUI 程序 ✓
- `C:\code\codex\main.py` → 检测为 GUI 程序 ✓
- `C:\code\codex\test\test_project\main.py` → 检测为控制台程序 ✓

### 2. 新窗口运行测试
✓ **通过**

- 程序成功在新控制台窗口中启动
- 窗口正常显示
- 进程 ID 正确返回

### 3. 全局快捷键测试
✓ **通过**

- 在输入框中按 F5 → 运行程序 ✓
- 在历史列表中按 F5 → 运行程序 ✓
- 在答案列表中按 F5 → 运行程序 ✓
- 在任意位置按 Ctrl+O → 打开文件夹对话框 ✓

## 功能特性

### 1. 智能程序类型检测

程序会自动检测主程序是否为 GUI 程序，通过检查以下导入语句：

```python
- import wx / from wx
- import tkinter / from tkinter
- import PyQt5 / from PyQt5
- import PyQt6 / from PyQt6
- import PySide / from PySide
- import kivy / from kivy
```

### 2. 不同运行模式

**GUI 程序**:
- 在新窗口中启动
- 不捕获输出
- 窗口独立显示
- 返回进程 ID

**控制台程序**:
- 在后台运行
- 捕获标准输出和错误输出
- 在控制台显示结果
- 5 分钟超时保护

### 3. 全局快捷键

- **Ctrl+O**: 在窗口任意位置按下，打开文件夹选择对话框
- **F5**: 在窗口任意位置按下，运行已载入项目的主程序

## 使用示例

### 示例 1: 运行 GUI 程序 (windowsZhuge)

```
1. 按 Ctrl+O
2. 选择 C:\code\windowsZhuge
3. 按 F5
4. 程序在新窗口中启动
5. 状态栏显示: "已启动：main.py (PID: 12345)"
```

控制台输出:
```
[运行信息]
  工作目录: C:\code\windowsZhuge
  主程序: C:\code\windowsZhuge\main.py
  Python: C:\code\windowsZhuge\.venv311\Scripts\python.exe
  命令: C:\code\windowsZhuge\.venv311\Scripts\python.exe C:\code\windowsZhuge\main.py
[开始运行...]
[检测到 GUI 程序，将在新窗口中运行]
[已启动] PID: 12345
```

### 示例 2: 运行控制台程序 (test_project)

```
1. 按 Ctrl+O
2. 选择 C:\code\codex\test\test_project
3. 按 F5
4. 程序在后台运行
5. 输出显示在控制台
6. 状态栏显示: "运行成功：main.py"
```

控制台输出:
```
[运行信息]
  工作目录: C:\code\codex\test\test_project
  主程序: C:\code\codex\test\test_project\main.py
  Python: C:\code\codex\.venv311\Scripts\python.exe
  命令: C:\code\codex\.venv311\Scripts\python.exe C:\code\codex\test\test_project\main.py
[开始运行...]
[检测到控制台程序，将捕获输出]
[运行成功] 退出码: 0
[标准输出]
============================================================
测试项目启动成功！
============================================================
Python 版本: 3.11.5 (tags/v3.11.5:cce6ba9, Aug 24 2023, 14:38:34) [MSC v.1936 64 bit (AMD64)]
当前时间: 2026-04-01 08:45:30
============================================================
这是一个测试程序，用于验证 F5 快速运行功能。
如果你看到这条消息，说明程序运行成功！
============================================================
```

## 技术实现细节

### 1. 全局快捷键实现

使用 wxPython 的 `EVT_CHAR_HOOK` 事件，在事件传播到具体控件之前拦截：

```python
def _on_char_hook(self, event):
    key = event.GetKeyCode()
    
    # 全局 Ctrl+O
    if key == ord('O') and event.ControlDown() and not event.AltDown():
        self._load_project_folder()
        return
    
    # 全局 F5
    if key == wx.WXK_F5 and not event.ControlDown() and not event.AltDown():
        if is_codex_model(self.selected_model) or is_claudecode_model(self.selected_model):
            self._quick_run_main_program()
            return
    
    event.Skip()
```

### 2. GUI 程序检测

读取主程序文件的前 5000 个字符，检查是否包含 GUI 库的导入语句：

```python
def _is_gui_program(self, main_file: str) -> bool:
    with open(main_file, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read(5000)
        
        gui_indicators = [
            'import wx', 'from wx',
            'import tkinter', 'from tkinter',
            # ... 其他 GUI 库
        ]
        
        for indicator in gui_indicators:
            if indicator in content:
                return True
        
        return False
```

### 3. 新窗口启动

在 Windows 上使用 `CREATE_NEW_CONSOLE` 标志：

```python
if os.name == 'nt':
    CREATE_NEW_CONSOLE = 0x00000010
    proc = subprocess.Popen(
        [python_cmd, main_file],
        cwd=cwd,
        creationflags=CREATE_NEW_CONSOLE,
        shell=False
    )
```

## 已知限制

1. **GUI 检测准确性**: 基于简单的字符串匹配，可能存在误判
2. **超时限制**: 控制台程序运行超过 5 分钟会被终止
3. **平台限制**: 新窗口功能仅在 Windows 上测试
4. **编码问题**: 某些特殊字符可能显示为乱码

## 后续改进建议

1. 添加配置选项，允许用户选择运行模式（新窗口/后台）
2. 支持自定义超时时间
3. 添加程序运行历史记录
4. 支持停止正在运行的程序
5. 改进 GUI 程序检测算法（使用 AST 分析）

## 总结

✓ 所有功能测试通过
✓ 全局快捷键工作正常
✓ GUI 程序窗口正常显示
✓ 控制台程序输出正常捕获

功能已完全修复并可以正常使用！
