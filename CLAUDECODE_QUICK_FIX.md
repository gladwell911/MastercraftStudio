# "未返回任何内容" 问题 - 快速解决指南

## 问题症状

在使用 Claude Code CLI 时出现：
```
未返回任何内容。
```

## 快速诊断

### 1. 检查 Claude Code 是否安装
```bash
claude --version
```

### 2. 测试基本功能
```bash
claude --print "test" --output-format stream-json --verbose
```

### 3. 查看错误信息中的调试信息
错误信息格式：
```
Claude Code CLI 未返回任何内容。调试信息：JSON 行数: X | Assistant 消息: Y | ...
```

## 根据调试信息诊断

### 情况 1：JSON 行数为 0
**问题**：Claude Code 没有返回任何数据
**原因**：
- Claude Code 进程异常退出
- 权限问题
- 网络问题

**解决**：
```bash
# 检查 Claude Code 是否正常
claude --print "test" --output-format stream-json

# 检查权限
which claude
```

### 情况 2：Assistant 消息为 0
**问题**：没有接收到 assistant 类型的消息
**原因**：
- Claude Code 版本不兼容
- 消息格式改变

**解决**：
```bash
# 更新 Claude Code
npm install -g @anthropic-ai/claude-code@latest
```

### 情况 3：文本项为 0
**问题**：接收到消息但无法提取文本
**原因**：
- 消息结构不符合预期
- 内容为空

**解决**：
- 检查 Claude Code 返回的数据格式
- 查看 stderr 错误信息

### 情况 4：解析错误 > 0
**问题**：JSON 解析失败
**原因**：
- 数据格式错误
- 编码问题

**解决**：
- 检查 Claude Code 输出
- 验证编码设置

## 常见解决方案

### 方案 1：更新 Claude Code
```bash
npm install -g @anthropic-ai/claude-code@latest
```

### 方案 2：检查权限配置
确保程序使用 `ClaudeCodeClient(full_auto=True)`

### 方案 3：增加超时时间
修改 `claudecode_client.py`：
```python
DEFAULT_TIMEOUT_SECONDS = 600  # 从 300 改为 600
```

### 方案 4：检查系统资源
```bash
# 检查内存
free -h

# 检查磁盘
df -h
```

### 方案 5：查看详细日志
直接运行 Claude Code 查看输出：
```bash
claude --print "你的问题" --output-format stream-json --verbose
```

## 改进措施

✅ **已实现**：
- 详细的调试信息
- 更好的错误处理
- 改进的日志记录

## 相关文档

- `CLAUDECODE_NO_CONTENT_FIX.md` - 详细诊断指南
- `CLAUDECODE_FIX_REPORT.md` - 修复报告
- `claudecode_client.py` - 客户端实现

## 获取帮助

1. 收集完整的错误信息（包含调试信息）
2. 运行诊断命令
3. 查看相关文档
4. 检查 Claude Code 版本
