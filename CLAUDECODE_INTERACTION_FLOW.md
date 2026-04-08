# Claude Code 用户交互流程设计

## 当前状态

目前的实现会在 UI 中显示请求消息，但还没有真正的交互界面。需要进一步改进以支持用户选择和输入。

## 设计方案

### 方案 1：在答案区域显示交互式界面（推荐）

**流程**：
```
1. Claude Code 返回 user_input 或 approval 消息
   ↓
2. 程序在答案区域显示请求和选项
   ↓
3. 用户在界面上点击选项或输入内容
   ↓
4. 程序捕获用户的选择
   ↓
5. 程序将用户回复写入 Claude Code 的 stdin
   ↓
6. Claude Code 继续执行
```

**优点**：
- ✅ 用户体验好
- ✅ 界面统一
- ✅ 易于操作
- ✅ 支持多种交互方式

**缺点**：
- ❌ 需要修改 UI 代码
- ❌ 需要处理异步交互

### 方案 2：弹出对话框

**流程**：
```
1. Claude Code 返回 user_input 或 approval 消息
   ↓
2. 程序弹出对话框显示请求
   ↓
3. 用户在对话框中选择或输入
   ↓
4. 用户点击"确定"按钮
   ↓
5. 程序将用户回复写入 Claude Code 的 stdin
   ↓
6. Claude Code 继续执行
```

**优点**：
- ✅ 实现简单
- ✅ 用户注意力集中
- ✅ 易于处理同步交互

**缺点**：
- ❌ 可能打断工作流
- ❌ 不够优雅

### 方案 3：输入框 + 按钮

**流程**：
```
1. Claude Code 返回 user_input 或 approval 消息
   ↓
2. 程序在答案区域显示请求
   ↓
3. 程序在界面底部显示输入框和按钮
   ↓
4. 用户输入内容或点击选项按钮
   ↓
5. 程序将用户回复写入 Claude Code 的 stdin
   ↓
6. Claude Code 继续执行
```

**优点**：
- ✅ 用户体验好
- ✅ 易于实现
- ✅ 支持多种交互

**缺点**：
- ❌ 需要修改 UI 布局

## 推荐实现方案

### 方案 1 + 方案 3 的混合方案

**特点**：
- 在答案区域显示请求和选项
- 提供点击按钮快速选择
- 提供输入框用于自定义输入
- 支持"发送"按钮提交

**具体流程**：

#### 步骤 1：显示请求

当 Claude Code 返回 `user_input` 消息时：

```
【Claude Code 需要你的输入】

问题 1 (q1)
修改模型显示名称
可选项：
1. 使用简短名称 (openclaw, codex, claudeCode)
2. 保持原名称 (openclaw/main, codex/main, claudecode/default)
3. 自定义名称

[按钮: 选项1] [按钮: 选项2] [按钮: 选项3]

或者输入自定义内容：
[输入框: ________________] [发送按钮]
```

#### 步骤 2：用户交互

用户可以：
- 点击"选项1"、"选项2"、"选项3"按钮快速选择
- 或在输入框中输入自定义内容，然后点击"发送"

#### 步骤 3：处理回复

程序捕获用户的选择或输入，然后：
```python
# 如果用户点击了选项按钮
user_reply = "1"  # 或 "2" 或 "3"

# 如果用户输入了自定义内容
user_reply = "自定义内容"

# 将回复写入 Claude Code 的 stdin
proc.stdin.write(user_reply + "\n")
proc.stdin.flush()
```

#### 步骤 4：Claude Code 继续执行

Claude Code 接收到用户的回复，继续执行任务。

## 实现步骤

### 第 1 步：修改 claudecode_client.py

添加事件机制来等待用户输入：

```python
class ClaudeCodeClient:
    def __init__(self, ...):
        self.user_input_event = threading.Event()
        self.user_input_value = ""
        self.approval_event = threading.Event()
        self.approval_value = ""

    def set_user_input(self, value: str):
        """设置用户输入"""
        self.user_input_value = value
        self.user_input_event.set()

    def set_approval(self, value: str):
        """设置批准结果"""
        self.approval_value = value
        self.approval_event.set()

    def wait_user_input(self, timeout: int = 300) -> str:
        """等待用户输入"""
        self.user_input_event.clear()
        self.user_input_event.wait(timeout=timeout)
        return self.user_input_value

    def wait_approval(self, timeout: int = 300) -> str:
        """等待批准"""
        self.approval_event.clear()
        self.approval_event.wait(timeout=timeout)
        return self.approval_value
```

### 第 2 步：修改 main.py 中的回调

```python
def on_user_input(params: dict) -> str:
    """处理用户输入请求"""
    from claudecode_remote_protocol import format_remote_user_input_request
    
    request_msg = format_remote_user_input_request(params)
    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n")
    
    # 显示交互式界面
    wx_call_after_if_alive(self._show_claudecode_user_input, turn_idx, params, client)
    
    # 等待用户输入（最多 5 分钟）
    user_reply = client.wait_user_input(timeout=300)
    return user_reply

def on_approval(params: dict) -> str:
    """处理批准请求"""
    from claudecode_remote_protocol import format_remote_approval_request
    
    request_msg = format_remote_approval_request(params)
    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要批准】\n{request_msg}\n\n")
    
    # 显示交互式界面
    wx_call_after_if_alive(self._show_claudecode_approval, turn_idx, params, client)
    
    # 等待批准（最多 5 分钟）
    approval_reply = client.wait_approval(timeout=300)
    return approval_reply
```

### 第 3 步：添加 UI 方法

```python
def _show_claudecode_user_input(self, turn_idx: int, params: dict, client: ClaudeCodeClient):
    """显示用户输入界面"""
    questions = params.get("questions", [])
    
    if len(questions) == 1:
        question = questions[0]
        options = question.get("options", [])
        
        # 创建按钮
        for idx, option in enumerate(options, start=1):
            label = option.get("label", f"选项{idx}")
            value = option.get("value", label)
            
            # 创建按钮并绑定事件
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, lambda evt, v=value: client.set_user_input(str(idx)))
        
        # 创建输入框
        input_box = wx.TextCtrl(self)
        send_btn = wx.Button(self, label="发送")
        send_btn.Bind(wx.EVT_BUTTON, lambda evt: client.set_user_input(input_box.GetValue()))

def _show_claudecode_approval(self, turn_idx: int, params: dict, client: ClaudeCodeClient):
    """显示批准界面"""
    # 创建"是"和"否"按钮
    yes_btn = wx.Button(self, label="是")
    no_btn = wx.Button(self, label="否")
    
    yes_btn.Bind(wx.EVT_BUTTON, lambda evt: client.set_approval("yes"))
    no_btn.Bind(wx.EVT_BUTTON, lambda evt: client.set_approval("no"))
```

## 交互示例

### 示例 1：用户输入选择

```
用户请求：
"修改一下主界面上模型组合框中模型的显示名称"

Claude Code 返回：
【Claude Code 需要你的输入】
问题 1 (q1)
选择修改方式
可选项：
1. 直接修改 MODEL_IDS 列表
2. 添加 MODEL_DISPLAY_NAMES 字典
3. 修改 model_id_from_display_name 函数

[按钮: 选项1] [按钮: 选项2] [按钮: 选项3]

用户操作：
点击"选项2"按钮

程序操作：
1. 捕获用户点击事件
2. 调用 client.set_user_input("2")
3. 将 "2" 写入 Claude Code 的 stdin
4. Claude Code 继续执行，选择方案 2

结果：
Claude Code 返回修改后的代码
```

### 示例 2：用户批准操作

```
Claude Code 返回：
【Claude Code 需要批准】
Claude Code 需要批准：
修改文件 main.py 中的 MODEL_DISPLAY_NAMES 字典

[按钮: 是] [按钮: 否]

用户操作：
点击"是"按钮

程序操作：
1. 捕获用户点击事件
2. 调用 client.set_approval("yes")
3. 将 "yes" 写入 Claude Code 的 stdin
4. Claude Code 继续执行

结果：
Claude Code 执行修改操作
```

## 实现优先级

### 优先级 1（立即实现）
- ✅ 在答案区域显示请求消息
- ✅ 添加事件机制等待用户输入
- ✅ 支持超时处理

### 优先级 2（后续实现）
- 添加点击按钮快速选择
- 添加输入框用于自定义输入
- 改进 UI 显示

### 优先级 3（可选）
- 添加历史记录
- 添加快捷键
- 添加自动化脚本

## 总结

**当前状态**：
- ✅ 可以显示请求消息
- ❌ 还不能真正交互

**改进方向**：
- 添加事件机制等待用户输入
- 在 UI 中添加交互式界面
- 支持点击按钮和输入框

**最终效果**：
- 用户可以在 UI 中看到 Claude Code 的请求
- 用户可以点击按钮或输入内容进行选择
- 程序自动将用户的选择发送给 Claude Code
- Claude Code 继续执行任务
