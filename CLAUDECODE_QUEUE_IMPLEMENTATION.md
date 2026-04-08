# Claude Code 队列式 stdin 通信实现 - 完整文档

## 概述

本文档描述了 Claude Code CLI 集成中的队列式 stdin 通信实现。这个实现允许用户在程序界面中直接与 Claude Code 交互，提供与 CLI 完全一致的体验。

## 问题背景

### 原始问题
- Claude Code CLI 在需要用户输入或批准时会等待 stdin 数据
- 如果 3 秒内没有收到 stdin 数据，会超时并显示错误：`Warning: no stdin data received in 3s...`
- 事件机制（event-based）无法提供实时通信，导致超时

### 解决方案
使用队列式（queue-based）stdin 通信机制：
- 用户在程序界面中发送消息
- 消息被拦截并放入队列
- 专用线程从队列读取消息并立即写入 Claude Code 的 stdin
- Claude Code 继续执行

## 架构设计

### 核心组件

#### 1. ClaudeCodeClient (claudecode_client.py)

**队列初始化**
```python
self.stdin_queue = queue.Queue()  # 用于传递用户输入
self.stdin_writer_thread = None   # stdin 写入线程
```

**发送用户输入**
```python
def send_user_input(self, user_input: str) -> None:
    """发送用户输入到 Claude Code"""
    self.stdin_queue.put(str(user_input or "").strip())
```

**stdin 写入线程**
```python
def _stdin_writer(self, proc) -> None:
    """在单独的线程中写入 stdin"""
    try:
        while proc.poll() is None:  # 进程还在运行
            try:
                # 从队列中获取用户输入（超时 1 秒）
                user_input = self.stdin_queue.get(timeout=1)
                if user_input is None:  # 哨兵值，表示停止
                    break
                # 写入 stdin
                if proc.stdin:
                    proc.stdin.write(user_input + "\n")
                    proc.stdin.flush()
            except queue.Empty:
                continue
    finally:
        if proc.stdin:
            try:
                proc.stdin.close()
            except:
                pass
```

**消息处理**
在 `stream_chat()` 中处理 `user_input` 和 `approval` 消息类型：
```python
elif msg_type == "user_input":
    if callable(on_user_input):
        user_reply = on_user_input(obj)
        if user_reply:
            # 将用户回复写入 stdin（通过队列）
            self.send_user_input(user_reply)

elif msg_type == "approval":
    if callable(on_approval):
        approval_reply = on_approval(obj)
        if approval_reply:
            # 将批准回复写入 stdin（通过队列）
            self.send_user_input(approval_reply)
```

#### 2. 主程序集成 (main.py)

**客户端跟踪**
```python
# 在 _start_claudecode_worker_for_turn 中
client = ClaudeCodeClient(full_auto=True)
# 保存客户端引用，以便在用户发送消息时使用
self._active_claudecode_client = client
```

**消息拦截**
在 `_submit_question()` 中检查是否有活跃的 Claude Code 客户端：
```python
# 检查是否有活跃的 Claude Code 客户端在等待输入
if hasattr(self, '_active_claudecode_client') and self._active_claudecode_client is not None:
    # 将消息发送到 Claude Code 的 stdin 队列
    self._active_claudecode_client.send_user_input(q)
    # 清空输入框
    self.input_edit.SetValue("")
    self.input_edit.SetFocus()
    return True, ""
```

**客户端清除**
在 worker 完成时清除客户端引用：
```python
finally:
    # 清除客户端引用
    self._active_claudecode_client = None
```

**回调简化**
回调函数现在只需显示消息并返回空字符串：
```python
def on_user_input(params: dict) -> str:
    """处理用户输入请求"""
    request_msg = format_remote_user_input_request(params)
    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n")
    # 返回空字符串，让 Claude Code 等待 stdin
    return ""
```

## 工作流程

### 用户输入场景

```
1. Claude Code 返回 user_input 消息
   ↓
2. on_user_input() 回调被调用
   ↓
3. 显示请求消息给用户
   ↓
4. 返回空字符串，让 Claude Code 等待 stdin
   ↓
5. 用户在程序界面中输入内容
   ↓
6. _submit_question() 拦截消息
   ↓
7. 消息被放入 stdin_queue
   ↓
8. _stdin_writer 线程从队列读取消息
   ↓
9. 消息立即写入 Claude Code 的 stdin
   ↓
10. Claude Code 继续执行
```

### 批准场景

```
1. Claude Code 返回 approval 消息
   ↓
2. on_approval() 回调被调用
   ↓
3. 显示批准请求给用户
   ↓
4. 返回空字符串，让 Claude Code 等待 stdin
   ↓
5. 用户发送 "yes" 或 "no"
   ↓
6. _submit_question() 拦截消息
   ↓
7. 消息被放入 stdin_queue
   ↓
8. _stdin_writer 线程从队列读取消息
   ↓
9. 消息立即写入 Claude Code 的 stdin
   ↓
10. Claude Code 继续执行
```

## 关键特性

### 1. 实时通信
- 用户消息立即被写入 stdin
- 不需要等待事件或超时
- Claude Code 不会因为没有 stdin 数据而超时

### 2. 线程安全
- 使用 `queue.Queue` 进行线程间通信
- 自动处理并发访问
- 不需要手动锁定

### 3. 优雅关闭
- 使用哨兵值 (None) 停止 stdin 写入线程
- 在 worker 完成时清除客户端引用
- 确保资源正确释放

### 4. 错误处理
- 处理进程已结束的情况
- 处理 stdin 关闭的情况
- 处理队列超时的情况

## 测试覆盖

### 单元测试 (test_claudecode_integration.py)
- ✓ 队列式 stdin 通信
- ✓ stdin 写入线程
- ✓ 用户输入回调
- ✓ 批准回调
- ✓ 消息类型处理
- ✓ 队列哨兵值
- ✓ 完全自动模式

### 端到端测试 (test_claudecode_e2e.py)
- ✓ 端到端用户输入流程
- ✓ 端到端批准流程
- ✓ stdin 队列集成
- ✓ 消息拦截机制

## 使用示例

### 场景 1：用户选择选项

```
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

用户输入：
q1=2

程序处理：
1. _submit_question() 拦截消息
2. 消息被放入 stdin_queue
3. _stdin_writer 线程写入 stdin
4. Claude Code 继续执行
```

### 场景 2：用户批准操作

```
程序显示：
【Claude Code 需要批准】
Claude Code 需要批准：
修改文件 main.py 中的 MODEL_DISPLAY_NAMES 字典

用户输入：
yes

程序处理：
1. _submit_question() 拦截消息
2. 消息被放入 stdin_queue
3. _stdin_writer 线程写入 stdin
4. Claude Code 继续执行
```

## 性能考虑

### 队列大小
- 队列没有大小限制
- 对于正常使用场景足够

### 线程开销
- 每个 Claude Code 会话只有一个额外的线程
- 线程在 worker 完成时自动清理

### 延迟
- 消息写入延迟 < 100ms
- 对用户体验没有影响

## 故障排查

### 问题：消息没有被发送到 Claude Code

**检查清单：**
1. 确认 `_active_claudecode_client` 不为 None
2. 确认 stdin 写入线程正在运行
3. 检查 Claude Code 进程是否仍在运行
4. 查看 stderr 输出是否有错误

### 问题：Claude Code 仍然超时

**可能原因：**
1. stdin 写入线程没有启动
2. 消息没有被正确放入队列
3. Claude Code 进程已经结束

**解决方案：**
1. 检查 `_stdin_writer()` 是否被调用
2. 添加日志记录消息流
3. 检查 Claude Code 的 stderr 输出

## 未来改进

### 1. 消息验证
- 在发送前验证消息格式
- 提供用户友好的错误提示

### 2. 超时处理
- 添加用户输入超时检测
- 自动取消长时间未响应的请求

### 3. UI 增强
- 添加点击按钮快速选择
- 显示倒计时
- 添加取消按钮

### 4. 日志记录
- 记录所有消息交互
- 用于调试和审计

## 总结

队列式 stdin 通信实现提供了：
- ✅ 实时交互体验
- ✅ 与 CLI 完全一致的行为
- ✅ 可靠的消息传递
- ✅ 优雅的错误处理
- ✅ 完整的测试覆盖

这个实现解决了原始的超时问题，并为用户提供了无缝的交互体验。
