# F5 快速运行功能 - 使用说明

## 功能概述

在使用 Codex 或 Claude Code 模型时，可以通过快捷键快速载入项目并运行主程序，类似于 VS Code 的 Ctrl+F5 功能。

## 快捷键

- **Ctrl+O**: 载入项目文件夹
- **F5**: 快速运行主程序

## 使用步骤

### 1. 载入项目文件夹 (Ctrl+O)

1. 在聊天界面按 **Ctrl+O**
2. 在弹出的文件夹选择对话框中，选择你的项目文件夹
3. 点击"确定"

载入成功后：
- 聊天记录中会显示：📁 已载入项目文件夹：`路径`
- 状态栏显示：已载入项目：项目名称
- 项目路径会保存到当前聊天记录中

### 2. 快速运行主程序 (F5)

1. 确保已选择 `codex` 或 `claudecode` 模型
2. 确保已通过 Ctrl+O 载入项目文件夹
3. 在聊天输入框中按 **F5**

程序会：
- 自动在项目文件夹中查找主程序
- 优先使用项目的虚拟环境（如果存在）
- 在后台运行程序
- 在控制台显示运行结果

## 支持的主程序文件名

程序会按以下优先级查找主程序：

1. `main.py`
2. `app.py`
3. `run.py`
4. `__main__.py`
5. `start.py`
6. `index.py`

## 虚拟环境支持

程序会自动检测并使用以下虚拟环境（按优先级）：

1. `.venv/Scripts/python.exe`
2. `venv/Scripts/python.exe`
3. `.venv311/Scripts/python.exe`
4. `env/Scripts/python.exe`

如果没有虚拟环境，则使用系统 Python。

## 运行结果

运行结果会显示在：
- **状态栏**: 显示运行状态（正在运行、运行成功、运行失败）
- **控制台**: 显示详细的运行信息、标准输出和错误输出

### 控制台输出示例

```
[运行信息]
  工作目录: C:\code\myproject
  主程序: C:\code\myproject\main.py
  Python: C:\code\myproject\.venv\Scripts\python.exe
  命令: C:\code\myproject\.venv\Scripts\python.exe C:\code\myproject\main.py
[开始运行...]
[运行成功] 退出码: 0
[标准输出]
Hello World!
Program finished.
```

## 与 AI 对话的集成

载入项目文件夹后，在聊天中提到的"程序"、"代码"、"文件"都会自动关联到已载入的项目：

**示例对话：**

```
用户: [按 Ctrl+O 载入项目]
系统: 📁 已载入项目文件夹：C:\code\myproject

用户: 帮我修改 main.py，添加一个新功能
AI: [会自动在 C:\code\myproject\main.py 中修改]

用户: [按 F5 运行]
系统: 正在运行：main.py...
系统: 运行成功：main.py
```

## 注意事项

1. **必须先载入项目**: 按 F5 前必须先按 Ctrl+O 载入项目文件夹
2. **模型限制**: F5 功能仅在选择 `codex` 或 `claudecode` 模型时可用
3. **超时设置**: 程序运行超时时间为 5 分钟
4. **后台运行**: 程序在后台线程运行，不会阻塞 GUI
5. **项目隔离**: 每个聊天记录独立保存项目路径，切换聊天时会自动切换项目

## 测试

项目包含完整的测试套件：

```bash
# 运行单元测试
python test/test_quick_run.py

# 运行集成测试
python test/test_integration.py

# 测试项目
python test/test_project/main.py
```

## 故障排除

### 问题：按 F5 提示"未载入项目文件夹"
**解决**: 先按 Ctrl+O 载入项目文件夹

### 问题：提示"未找到主程序文件"
**解决**: 确保项目文件夹中包含支持的主程序文件名（main.py, app.py 等）

### 问题：程序运行失败
**解决**: 
1. 查看控制台的详细错误信息
2. 检查项目依赖是否已安装
3. 确认虚拟环境是否正确配置

### 问题：F5 没有反应
**解决**:
1. 确认已选择 codex 或 claudecode 模型
2. 确认焦点在聊天输入框中
3. 查看控制台是否有错误信息

## 技术实现

- **快捷键绑定**: 通过 wxPython 的 `EVT_KEY_DOWN` 事件处理
- **文件夹选择**: 使用 `wx.DirDialog`
- **程序运行**: 使用 `subprocess.run` 在后台线程执行
- **状态管理**: 项目路径保存在聊天记录的 `project_folder` 字段
- **工作目录**: 通过 `_workspace_dir_for_codex()` 方法统一管理

## 更新日志

### v1.0 (2026-04-01)
- ✓ 实现 Ctrl+O 载入项目文件夹
- ✓ 实现 F5 快速运行主程序
- ✓ 支持虚拟环境自动检测
- ✓ 添加完整的测试套件
- ✓ 集成到聊天上下文
