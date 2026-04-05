# Claude Code 全自动模式配置

## 修改内容

### 1. claudecode_client.py
修改 `_build_command` 方法，使用 `--dangerously-skip-permissions` 参数替代 `--permission-mode bypassPermissions`。

**修改位置**: `claudecode_client.py` 第 160-181 行

**关键变化**:
```python
# 之前
cmd.extend(["--permission-mode", "bypassPermissions"])

# 现在
cmd.extend(["--dangerously-skip-permissions"])
```

### 2. main.py
修改 `ClaudeCodeClient` 实例化，启用 `full_auto=True` 模式。

**修改位置**: `main.py` 第 3734 行

**关键变化**:
```python
# 之前
c = ClaudeCodeClient()

# 现在
c = ClaudeCodeClient(full_auto=True)
```

## 参数说明

### --dangerously-skip-permissions
这是 Claude Code CLI 提供的最强力的权限跳过选项：

- **功能**: 完全绕过所有权限检查，不会有任何确认提示
- **适用场景**: 自动化脚本、可信环境、沙箱环境
- **安全警告**: 仅在完全信任的环境中使用

### --permission-mode bypassPermissions
这是之前使用的选项：

- **功能**: 绕过大部分权限检查
- **限制**: 某些操作仍可能需要确认
- **区别**: 比 `--dangerously-skip-permissions` 更保守

## 测试结果

### 命令行参数验证

✓ **默认模式**: 不包含任何权限跳过参数
✓ **auto_approve 模式**: 包含 `--dangerously-skip-permissions`
✓ **full_auto 模式**: 包含 `--dangerously-skip-permissions` + `--append-system-prompt`

### 完整命令示例

使用 `ClaudeCodeClient(full_auto=True)` 生成的命令：

```bash
claude.cmd \
  --print "用户问题" \
  --output-format stream-json \
  --verbose \
  --dangerously-skip-permissions \
  --append-system-prompt "重要指示：你需要完全自主地完成任务，不要使用 AskUserQuestion 工具询问用户选择方案。当有多个实现方案时，请根据最佳实践自行选择最合适的方案并直接实施。只在遇到无法解决的错误或需要用户提供额外信息（如 API 密钥、配置参数等）时才询问用户。"
```

## 工作原理

### 1. 权限跳过
`--dangerously-skip-permissions` 参数告诉 Claude Code CLI：
- 不要询问文件读写权限
- 不要询问命令执行权限
- 不要询问网络访问权限
- 不要询问任何其他权限

### 2. 自主决策
`--append-system-prompt` 参数添加系统提示，指导 Claude：
- 不使用 `AskUserQuestion` 工具
- 自行选择最佳实现方案
- 只在真正需要用户输入时才询问（如 API 密钥）

### 3. 组合效果
两个参数结合使用，实现完全自动化：
- **工具层面**: 跳过所有权限确认
- **AI 层面**: 避免询问实现方案

## 使用方式

### 在项目中使用

程序已经自动配置为全自动模式，无需额外设置：

```python
# main.py 中已配置
c = ClaudeCodeClient(full_auto=True)
full, new_sid = c.stream_chat(question, session_id=session_id, on_delta=on_delta)
```

### 手动使用（如需自定义）

```python
from claudecode_client import ClaudeCodeClient

# 完全自动模式（推荐）
client = ClaudeCodeClient(full_auto=True)

# 仅跳过权限（不添加系统提示）
client = ClaudeCodeClient(auto_approve=True)

# 默认模式（需要手动确认）
client = ClaudeCodeClient()
```

## 安全建议

### ⚠️ 重要警告

`--dangerously-skip-permissions` 会完全跳过权限检查，这意味着：

1. **文件操作**: Claude 可以读写任何文件
2. **命令执行**: Claude 可以执行任何系统命令
3. **网络访问**: Claude 可以访问任何网络资源

### 安全使用建议

1. **仅在可信环境使用**: 只在你完全信任的项目和环境中使用
2. **定期检查**: 定期查看 Claude 执行的操作
3. **备份重要数据**: 在使用前备份重要文件
4. **限制工作目录**: 通过 Ctrl+O 载入特定项目文件夹，限制操作范围
5. **使用版本控制**: 使用 Git 等版本控制系统，便于回滚

### 适用场景

✓ **适合使用**:
- 个人开发项目
- 沙箱测试环境
- 自动化脚本
- 可信的代码库

✗ **不适合使用**:
- 生产环境
- 包含敏感数据的项目
- 共享开发环境
- 不熟悉的代码库

## 对比表

| 特性 | 默认模式 | auto_approve | full_auto |
|------|---------|--------------|-----------|
| 权限确认 | 需要 | 跳过 | 跳过 |
| 方案询问 | 可能 | 可能 | 避免 |
| 系统提示 | 无 | 无 | 有 |
| 自动化程度 | 低 | 中 | 高 |
| 安全性 | 高 | 中 | 低 |
| 推荐场景 | 生产环境 | 测试环境 | 开发环境 |

## 故障排除

### 问题：仍然有确认提示

**可能原因**:
1. 使用了旧版本的代码
2. 没有重启程序

**解决方案**:
1. 确认 `main.py` 第 3734 行是 `ClaudeCodeClient(full_auto=True)`
2. 确认 `claudecode_client.py` 使用 `--dangerously-skip-permissions`
3. 重启程序

### 问题：Claude 仍然询问实现方案

**可能原因**:
系统提示没有生效

**解决方案**:
检查命令行参数是否包含 `--append-system-prompt`，运行测试脚本验证：
```bash
python test/test_claudecode_params.py
```

## 测试

运行测试脚本验证配置：

```bash
cd C:\code\codex
.venv311\Scripts\python.exe test\test_claudecode_params.py
```

预期输出：
```
✓ 包含 --dangerously-skip-permissions
✓ 包含 --append-system-prompt
✓ 不包含 --permission-mode

✓ 所有检查通过！
```

## 总结

✅ 已修改为使用 `--dangerously-skip-permissions`
✅ 已启用 `full_auto=True` 模式
✅ 已添加自主决策系统提示
✅ 所有测试通过

现在在程序中使用 Claude Code 时，将完全自动执行，不会有任何确认提示！
