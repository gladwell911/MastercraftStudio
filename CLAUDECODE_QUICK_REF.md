# Claude Code 集成 - 快速参考

## 修复摘要

✅ **已修复 2 个关键问题**

| 问题 | 状态 | 修复方案 |
|------|------|--------|
| 权限配置不一致 | ✅ | 使用 `full_auto=True` |
| 缺少流式处理 | ✅ | 添加 `on_delta` 回调 |

## 修改位置

### main.py

**位置 1**：第 1969-1982 行
```python
def _start_claudecode_worker_for_turn(self, ...):
    # 改动：ClaudeCodeClient(full_auto=True) + on_delta
```

**位置 2**：第 3185-3191 行
```python
elif is_claudecode_model(model):
    # 改动：ClaudeCodeClient(full_auto=True) + on_delta
```

## 权限模式

```
默认模式
  └─ 需要用户交互

auto_approve 模式
  └─ 自动批准工具调用

full_auto 模式 ← 现在使用
  ├─ 跳过所有权限确认
  ├─ 自主决策
  └─ 实时流式显示
```

## 生成的命令

```bash
claude --print "问题" \
  --output-format stream-json \
  --verbose \
  --dangerously-skip-permissions \
  --append-system-prompt "自主决策提示..."
```

## 验证

```bash
# 语法检查
python -m py_compile main.py

# 参数测试
python test/test_claudecode_params.py
```

## 关键改动

### 之前
```python
client = ClaudeCodeClient()
full_text, new_session_id = client.stream_chat(question, session_id=session_id)
```

### 之后
```python
client = ClaudeCodeClient(full_auto=True)
def on_delta(delta):
    wx_call_after_if_alive(self._on_delta, turn_idx, delta)
full_text, new_session_id = client.stream_chat(
    question, 
    session_id=session_id, 
    on_delta=on_delta
)
```

## 效果

- ✅ Claude Code 全自动执行
- ✅ 跳过所有权限确认
- ✅ 实时流式显示
- ✅ 自主决策，减少询问
- ✅ Session 正确管理

## 文档

- `CLAUDECODE_FIX_REPORT.md` - 详细报告
- `CLAUDECODE_FIXES.md` - 修复总结
- `test/test_claudecode_params.py` - 参数测试
