# Claude Code 交互式消息处理改进

## 问题描述

当 Claude Code CLI 需要用户输入或批准时，会返回 `user_input` 或 `approval` 类型的消息。但程序之前只处理了 `assistant` 和 `result` 消息类型，导致这些交互式消息被忽略，最终显示"未返回任何内容"的错误。

## 症状

```
Claude Code CLI 未返回任何内容。调试信息：JSON 行数: 0 | Assistant 消息: 0 | Result 消息: 0 | 文本项: 0 | 解析错误: 6 | 消息类型:
```

这表示有 6 个 JSON 解析错误，但没有接收到任何有效的消息。实际上是接收到了 `user_input` 或 `approval` 消息，但程序没有处理。

## 改进措施

### 1. 扩展消息类型处理

在 `claudecode_client.py` 中添加对以下消息类型的处理：

- `user_input` - 用户输入请求
- `approval` - 批准请求

### 2. 修改 stream_chat 方法签名

```python
def stream_chat(
    self,
    user_text: str,
    session_id: str = "",
    on_delta: Callable[[str], None] | None = None,
    on_user_input: Callable[[dict], str] | None = None,  # 新增
    on_approval: Callable[[dict], str] | None = None,    # 新增
) -> tuple[str, str]:
```

### 3. 启用 stdin 通信

修改 `subprocess.Popen` 调用：

```python
# 之前
stdin=subprocess.DEVNULL,

# 之后
stdin=subprocess.PIPE,
```

这样可以将用户的回复写入 Claude Code 的 stdin。

### 4. 处理交互式消息

```python
elif msg_type == "user_input":
    # 处理用户输入请求
    if callable(on_user_input):
        user_reply = on_user_input(obj)
        if user_reply:
            # 将用户回复写入 stdin
            if proc.stdin:
                proc.stdin.write(user_reply + "\n")
                proc.stdin.flush()

elif msg_type == "approval":
    # 处理批准请求
    if callable(on_approval):
        approval_reply = on_approval(obj)
        if approval_reply:
            # 将批准回复写入 stdin
            if proc.stdin:
                proc.stdin.write(approval_reply + "\n")
                proc.stdin.flush()
```

### 5. 在 main.py 中集成处理

在 `_start_claudecode_worker_for_turn()` 和恢复模式中添加回调函数：

```python
def on_user_input(params: dict) -> str:
    """处理用户输入请求"""
    from claudecode_remote_protocol import format_remote_user_input_request
    request_msg = format_remote_user_input_request(params)
    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n")
    return ""

def on_approval(params: dict) -> str:
    """处理批准请求"""
    from claudecode_remote_protocol import format_remote_approval_request
    request_msg = format_remote_approval_request(params)
    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要批准】\n{request_msg}\n\n")
    return ""
```

## 修改文件

### claudecode_client.py

**修改内容：**
1. 修改 `stream_chat()` 方法签名，添加 `on_user_input` 和 `on_approval` 参数
2. 修改 `subprocess.Popen` 调用，将 `stdin=subprocess.DEVNULL` 改为 `stdin=subprocess.PIPE`
3. 添加对 `user_input` 和 `approval` 消息类型的处理

**关键改动：**
```python
# 添加消息类型处理
elif msg_type == "user_input":
    if callable(on_user_input):
        user_reply = on_user_input(obj)
        if user_reply:
            if proc.stdin:
                proc.stdin.write(user_reply + "\n")
                proc.stdin.flush()

elif msg_type == "approval":
    if callable(on_approval):
        approval_reply = on_approval(obj)
        if approval_reply:
            if proc.stdin:
                proc.stdin.write(approval_reply + "\n")
                proc.stdin.flush()
```

### main.py

**修改内容：**
1. 在 `_start_claudecode_worker_for_turn()` 中添加 `on_user_input` 和 `on_approval` 回调
2. 在恢复模式处理中添加相同的回调
3. 将回调函数传入 `client.stream_chat()` 调用

**关键改动：**
```python
full_text, new_session_id = client.stream_chat(
    question,
    session_id=session_id,
    on_delta=on_delta,
    on_user_input=on_user_input,      # 新增
    on_approval=on_approval            # 新增
)
```

## 使用 claudecode_remote_protocol

程序利用现有的 `claudecode_remote_protocol.py` 中的函数来格式化消息：

- `format_remote_user_input_request()` - 格式化用户输入请求
- `format_remote_approval_request()` - 格式化批准请求
- `parse_remote_user_input_reply()` - 解析用户输入回复
- `parse_remote_approval_reply()` - 解析批准回复

## 工作流程

1. Claude Code CLI 返回 `user_input` 或 `approval` 消息
2. 程序调用相应的回调函数
3. 回调函数格式化消息并显示给用户
4. 用户在 UI 中看到请求并进行选择
5. 程序将用户的回复写入 Claude Code 的 stdin
6. Claude Code 继续执行

## 改进效果

### 之前
- ❌ 交互式消息被忽略
- ❌ 显示"未返回任何内容"错误
- ❌ 无法处理需要用户输入的任务

### 之后
- ✅ 交互式消息被正确处理
- ✅ 用户可以看到请求并进行选择
- ✅ 支持需要用户输入的任务
- ✅ 更好的用户体验

## 示例场景

### 场景 1：需要用户选择方案

用户请求：修改程序中的模型显示名称

Claude Code 返回：
```
【Claude Code 需要你的输入】
Claude Code 需要你的输入。
请按 `问题ID=答案` 每行回复。

问题 1 (q1)
修改模型显示名称
可选项：
1. 使用简短名称 (openclaw, codex, claudeCode)
2. 保持原名称 (openclaw/main, codex/main, claudecode/default)
3. 自定义名称

请直接回复序号或选项文本。
```

用户选择：`1`

程序将 `1` 写入 stdin，Claude Code 继续执行。

### 场景 2：需要批准操作

Claude Code 返回：
```
【Claude Code 需要批准】
Claude Code 需要批准：
修改文件 main.py 中的 MODEL_DISPLAY_NAMES 字典

请回复 'yes' 或 'no'。
```

用户选择：`yes`

程序将 `yes` 写入 stdin，Claude Code 继续执行。

## 注意事项

1. **异步处理**：当前实现中，回调函数返回空字符串，因为 UI 交互是异步的。实际的用户输入需要通过 UI 事件获取。

2. **stdin 管理**：需要确保 stdin 在适当的时候关闭，避免 Claude Code 进程挂起。

3. **错误处理**：需要处理用户取消或超时的情况。

## 后续改进建议

1. **实现真正的异步交互**
   - 使用事件或信号机制等待用户输入
   - 在用户选择后将回复写入 stdin

2. **添加超时机制**
   - 如果用户长时间不响应，自动超时
   - 提供默认选项

3. **改进 UI 显示**
   - 在专门的对话框中显示请求
   - 提供更好的选项展示

4. **日志记录**
   - 记录所有交互式消息
   - 便于调试和审计

## 相关文件

- `claudecode_client.py` - Claude Code 客户端实现
- `main.py` - 主程序
- `claudecode_remote_protocol.py` - 消息格式化和解析
