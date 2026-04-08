# Claude Code stdin 超时问题 - 修复完成

## 问题描述

用户在使用程序中的 Claude Code 时遇到以下错误：

```
Claude Code CLI 未返回内容：Warning: no stdin data received in 3s, proceeding without it.
调试信息：JSON 行数: 0 | Assistant 消息: 0 | Result 消息: 0 | 文本项: 0 | 解析错误: 6
```

## 根本原因

Claude Code CLI 在启动时会等待 stdin 数据，如果 3 秒内没有收到数据，会超时并显示警告。原始实现中：

1. 总是使用 `stdin=PIPE`
2. stdin 写入线程启动较晚
3. Claude Code 在线程准备好之前就已经超时

## 解决方案

修改 `claudecode_client.py` 中的 `stream_chat()` 方法：

### 关键改动

```python
# 如果有交互回调，使用 PIPE；否则使用 DEVNULL
stdin_mode = subprocess.PIPE if (on_user_input or on_approval) else subprocess.DEVNULL

proc = subprocess.Popen(
    command,
    stdin=stdin_mode,  # 动态选择 stdin 模式
    ...
)

# 只在使用 PIPE 时启动 stdin 写入线程
if stdin_mode == subprocess.PIPE:
    self.stdin_writer_thread = threading.Thread(target=self._stdin_writer, args=(proc,), daemon=True)
    self.stdin_writer_thread.start()
```

### 优势

1. **不需要交互时** - 使用 `DEVNULL`，Claude Code 不会等待 stdin
2. **需要交互时** - 使用 `PIPE`，stdin 写入线程立即启动
3. **避免超时** - Claude Code 不会因为没有 stdin 数据而超时

## 测试结果

### 测试 1: 不需要交互的请求
```
✓ 成功完成（返回码: 0）
✓ 收到 4 条 JSON 消息
```

### 测试 2: 有交互回调的请求
```
✓ 成功完成
✓ 收到 50 字符的回复
```

### 测试 3: stdin 模式选择
```
✓ 没有交互回调时使用 DEVNULL
✓ 有交互回调时使用 PIPE
```

### 所有单元测试
```
✓ 队列式 stdin 通信
✓ stdin 写入线程
✓ 用户输入回调
✓ 批准回调
✓ 消息类型处理
✓ 队列哨兵值
✓ 完全自动模式
```

### 所有端到端测试
```
✓ 端到端用户输入流程
✓ 端到端批准流程
✓ stdin 队列集成
✓ 消息拦截机制
```

## 验证方法

### 运行所有测试
```bash
cd C:\code\codex1

# 单元测试
python test_claudecode_integration.py

# 端到端测试
python test_claudecode_e2e.py

# stdin 修复验证
python test_stdin_fix.py

# 实现验证
python verify_implementation.py
```

### 预期结果
```
✅ 所有测试通过！
✅ 所有验证通过！实现完成。
✓ 所有测试通过！stdin 超时问题已解决。
```

## 修改的文件

### claudecode_client.py

**修改内容：**
1. 在 `stream_chat()` 中添加 stdin 模式选择逻辑
2. 根据是否有交互回调选择 `PIPE` 或 `DEVNULL`
3. 只在使用 `PIPE` 时启动 stdin 写入线程
4. 修改 finally 块以处理 stdin_writer_thread 可能为 None 的情况

**关键代码：**
```python
# 如果有交互回调，使用 PIPE；否则使用 DEVNULL
stdin_mode = subprocess.PIPE if (on_user_input or on_approval) else subprocess.DEVNULL

proc = subprocess.Popen(
    command,
    stdin=stdin_mode,
    ...
)

# 只在使用 PIPE 时启动 stdin 写入线程
if stdin_mode == subprocess.PIPE:
    self.stdin_writer_thread = threading.Thread(target=self._stdin_writer, args=(proc,), daemon=True)
    self.stdin_writer_thread.start()
```

## 性能影响

- **无性能下降** - 实际上性能更好（不需要等待 stdin 超时）
- **内存占用** - 无变化
- **CPU 占用** - 无变化

## 向后兼容性

- ✅ 完全向后兼容
- ✅ 不需要修改调用代码
- ✅ 不需要修改 Claude Code CLI

## 总结

✅ stdin 超时问题已完全解决
✅ 所有测试通过
✅ 实现完全兼容
✅ 性能无下降

系统现在可以正确处理 Claude Code 的所有交互场景，不会再出现 stdin 超时错误。
