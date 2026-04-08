# Claude Code 用户交互 - 完整使用指南

## 当前实现状态

✅ **已实现**：
- 显示 Claude Code 的请求消息
- 等待用户输入或批准
- 支持超时处理（5 分钟）
- 验证用户回复格式

❌ **待实现**：
- UI 中的点击按钮
- 输入框和发送按钮
- 可视化的交互界面

## 操作流程

### 场景 1：Claude Code 需要用户输入

#### 步骤 1：Claude Code 返回请求

```
【Claude Code 需要你的输入】
Claude Code 需要你的输入。
请按 `问题ID=答案` 每行回复。

问题 1 (q1)
选择修改方式
可选项：
1. 直接修改 MODEL_IDS 列表
2. 添加 MODEL_DISPLAY_NAMES 字典
3. 修改 model_id_from_display_name 函数

请直接回复序号或选项文本。
```

#### 步骤 2：用户操作

**当前方式**（文本输入）：
- 在程序的输入框中输入：`q1=2`
- 或输入：`q1=添加 MODEL_DISPLAY_NAMES 字典`
- 点击"发送"按钮

**未来方式**（UI 按钮）：
- 直接点击"选项2"按钮
- 或在输入框中输入自定义内容

#### 步骤 3：程序处理

程序会：
1. 捕获用户的输入
2. 验证格式是否正确
3. 将输入发送给 Claude Code
4. Claude Code 继续执行

#### 步骤 4：Claude Code 继续执行

Claude Code 接收到用户的选择，继续执行任务。

### 场景 2：Claude Code 需要用户批准

#### 步骤 1：Claude Code 返回请求

```
【Claude Code 需要批准】
Claude Code 需要批准：
修改文件 main.py 中的 MODEL_DISPLAY_NAMES 字典

请回复 'yes' 或 'no'。
```

#### 步骤 2：用户操作

**当前方式**（文本输入）：
- 在程序的输入框中输入：`yes` 或 `no`
- 点击"发送"按钮

**未来方式**（UI 按钮）：
- 直接点击"是"或"否"按钮

#### 步骤 3：程序处理

程序会：
1. 捕获用户的选择
2. 验证格式是否正确
3. 将选择发送给 Claude Code
4. Claude Code 继续执行

## 代码实现细节

### 1. 事件机制

在 `claudecode_client.py` 中添加了事件机制：

```python
class ClaudeCodeClient:
    def __init__(self, ...):
        # 用户交互事件
        self.user_input_event = threading.Event()
        self.user_input_value = ""
        self.approval_event = threading.Event()
        self.approval_value = ""

    def set_user_input(self, value: str) -> None:
        """设置用户输入并触发事件"""
        self.user_input_value = str(value or "").strip()
        self.user_input_event.set()

    def set_approval(self, value: str) -> None:
        """设置批准结果并触发事件"""
        self.approval_value = str(value or "").strip()
        self.approval_event.set()

    def wait_user_input(self, timeout: int = 300) -> str:
        """等待用户输入（阻塞）"""
        self.user_input_event.clear()
        self.user_input_value = ""
        if self.user_input_event.wait(timeout=timeout):
            return self.user_input_value
        else:
            return ""

    def wait_approval(self, timeout: int = 300) -> str:
        """等待批准结果（阻塞）"""
        self.approval_event.clear()
        self.approval_value = ""
        if self.approval_event.wait(timeout=timeout):
            return self.approval_value
        else:
            return ""
```

### 2. 回调函数

在 `main.py` 中的回调函数现在真正等待用户输入：

```python
def on_user_input(params: dict) -> str:
    """处理用户输入请求"""
    # 显示请求消息
    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n")

    # 显示交互式界面（如果有回调）
    if hasattr(self, '_show_claudecode_user_input'):
        wx_call_after_if_alive(self._show_claudecode_user_input, turn_idx, params, client)

    # 等待用户输入（最多 5 分钟）
    user_reply = client.wait_user_input(timeout=300)

    if user_reply:
        # 验证用户回复格式
        answers, error = parse_remote_user_input_reply(params, user_reply)
        if error:
            wx_call_after_if_alive(self._on_delta, turn_idx, f"\n❌ 输入错误：{error}\n")
            return ""
        return user_reply
    else:
        wx_call_after_if_alive(self._on_delta, turn_idx, "\n⏱️ 输入超时，已取消\n")
        return ""
```

### 3. 工作流程

```
Claude Code 返回消息
    ↓
程序调用 on_user_input() 或 on_approval()
    ↓
显示请求消息
    ↓
调用 client.wait_user_input() 或 client.wait_approval()
    ↓
等待用户输入（阻塞）
    ↓
用户在 UI 中输入或点击按钮
    ↓
程序调用 client.set_user_input() 或 client.set_approval()
    ↓
事件被触发，wait_*() 返回用户的输入
    ↓
验证用户回复格式
    ↓
将用户回复写入 Claude Code 的 stdin
    ↓
Claude Code 继续执行
```

## 使用示例

### 示例 1：用户选择选项

```
用户请求：
"修改一下主界面上模型组合框中模型的显示名称"

程序显示：
【Claude Code 需要你的输入】
Claude Code 需要你的输入。
请按 `问题ID=答案` 每行回复。

问题 1 (q1)
选择修改方式
可选项：
1. 直接修改 MODEL_IDS 列表
2. 添加 MODEL_DISPLAY_NAMES 字典
3. 修改 model_id_from_display_name 函数

请直接回复序号或选项文本。

用户输入：
q1=2

程序处理：
1. 验证格式：✓ 正确
2. 将 "q1=2" 写入 Claude Code 的 stdin
3. Claude Code 继续执行

结果：
Claude Code 选择方案 2，返回修改后的代码
```

### 示例 2：用户批准操作

```
程序显示：
【Claude Code 需要批准】
Claude Code 需要批准：
修改文件 main.py 中的 MODEL_DISPLAY_NAMES 字典

请回复 'yes' 或 'no'。

用户输入：
yes

程序处理：
1. 验证格式：✓ 正确
2. 将 "yes" 写入 Claude Code 的 stdin
3. Claude Code 继续执行

结果：
Claude Code 执行修改操作
```

### 示例 3：用户输入超时

```
程序显示：
【Claude Code 需要你的输入】
...

用户没有在 5 分钟内输入

程序处理：
1. 等待超时
2. 显示：⏱️ 输入超时，已取消
3. 返回空字符串给 Claude Code

结果：
Claude Code 可能会重新提示或取消操作
```

## 后续改进

### 优先级 1：改进 UI 交互

添加点击按钮快速选择：

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
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, lambda evt, v=str(idx): client.set_user_input(f"q1={v}"))
```

### 优先级 2：改进输入框

添加输入框和发送按钮：

```python
# 创建输入框
input_box = wx.TextCtrl(self)
send_btn = wx.Button(self, label="发送")
send_btn.Bind(wx.EVT_BUTTON, lambda evt: client.set_user_input(input_box.GetValue()))
```

### 优先级 3：改进显示

- 添加倒计时显示
- 添加取消按钮
- 改进错误提示

## 总结

**当前状态**：
- ✅ 可以显示请求消息
- ✅ 可以等待用户输入
- ✅ 可以验证用户回复
- ✅ 可以处理超时

**操作方式**：
- 用户在输入框中输入内容
- 点击"发送"按钮
- 程序将输入发送给 Claude Code

**未来改进**：
- 添加点击按钮快速选择
- 改进 UI 显示
- 添加更多交互方式

**关键特性**：
- 支持多问题输入
- 支持选项选择
- 支持自定义输入
- 支持超时处理
- 支持格式验证
