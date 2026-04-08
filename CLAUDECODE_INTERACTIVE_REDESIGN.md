# Claude Code 交互式实现 - 完整重设计

## 问题分析

当前问题：
1. stdin 被设置为 PIPE，但没有真正的交互机制
2. Claude Code 在 3 秒内没有收到 stdin 数据，所以超时了
3. 需要实现真正的双向通信

## 解决方案

### 核心思路

不使用 stdin=PIPE，而是使用以下方案：

1. **方案 A：使用临时文件进行交互**
   - Claude Code 返回 user_input 或 approval 消息
   - 程序显示请求
   - 用户通过发送消息提供输入
   - 程序将用户输入写入临时文件
   - Claude Code 从临时文件读取输入

2. **方案 B：使用 stdin=DEVNULL + 重新启动**
   - 第一次调用：Claude Code 返回 user_input 消息
   - 程序显示请求
   - 用户发送消息
   - 程序使用 --resume 重新启动 Claude Code，并在系统提示中包含用户的选择

3. **方案 C：使用 stdin=PIPE + 主动写入**
   - 在单独的线程中监听用户输入
   - 当用户发送消息时，立即写入 stdin
   - Claude Code 继续执行

### 推荐方案：方案 C（最接近 CLI 体验）

**优点**：
- 最接近 CLI 的交互体验
- 支持实时交互
- 不需要临时文件
- 不需要重新启动

**实现步骤**：

1. 启用 stdin=PIPE
2. 创建一个输入监听线程
3. 当 Claude Code 返回 user_input 或 approval 消息时：
   - 显示请求
   - 等待用户发送消息
   - 将用户消息写入 stdin
   - Claude Code 继续执行

## 实现细节

### 修改 claudecode_client.py

```python
class ClaudeCodeClient:
    def __init__(self, ...):
        self.stdin_queue = queue.Queue()  # 用于传递用户输入
        self.stdin_writer_thread = None
        
    def _stdin_writer(self, proc):
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
        except Exception as e:
            print(f"stdin writer error: {e}")
        finally:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except:
                    pass
    
    def send_user_input(self, user_input: str):
        """发送用户输入到 Claude Code"""
        self.stdin_queue.put(user_input)
```

### 修改 main.py

```python
def on_user_input(params: dict) -> str:
    """处理用户输入请求"""
    from claudecode_remote_protocol import format_remote_user_input_request
    
    request_msg = format_remote_user_input_request(params)
    
    # 显示请求消息
    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n")
    
    # 显示提示：用户需要发送消息
    wx_call_after_if_alive(self._on_delta, turn_idx, "请在下方输入框中输入你的选择，然后发送消息。\n")
    
    # 返回空字符串，让 Claude Code 等待 stdin
    return ""
```

## 关键改进

1. **不再使用事件等待**
   - 改为使用队列
   - 用户发送消息时，直接写入 stdin

2. **实时交互**
   - Claude Code 等待 stdin
   - 用户发送消息
   - 程序立即写入 stdin
   - Claude Code 继续执行

3. **完全兼容 CLI**
   - 用户体验与 CLI 完全一致
   - 支持所有 Claude Code 的交互功能

## 集成测试计划

1. **测试用户输入**
   - 发送需要用户选择的请求
   - 验证 Claude Code 显示请求
   - 用户发送选择
   - 验证 Claude Code 继续执行

2. **测试用户批准**
   - 发送需要批准的请求
   - 验证 Claude Code 显示请求
   - 用户发送批准/拒绝
   - 验证 Claude Code 继续执行

3. **测试超时处理**
   - 发送需要用户输入的请求
   - 不发送任何消息
   - 验证 Claude Code 超时处理

4. **测试错误处理**
   - 发送无效的输入
   - 验证 Claude Code 错误处理

## 预期结果

✅ 用户可以在 UI 中看到 Claude Code 的请求
✅ 用户可以发送消息进行选择或批准
✅ 程序自动将用户消息写入 Claude Code 的 stdin
✅ Claude Code 继续执行
✅ 完全兼容 CLI 体验
