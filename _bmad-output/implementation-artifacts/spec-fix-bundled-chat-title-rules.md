---
title: '修复打包版聊天标题规则资源缺失'
type: 'bugfix'
created: '2026-09-13'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 打包版 mc 在首次发送问题并生成聊天标题时，会从发布目录之外的 `rc/assets/chat_title_rules.json` 读取规则；发布目录没有该源码资源时抛出 `FileNotFoundError`，使 Codex 消息无法发送。

**Approach:** 将共享标题规则作为 PyInstaller 数据文件纳入标准 mc 包，并让冻结运行时优先解析包内资源，同时保留源码运行时对兄弟 rc 仓库规则文件的现有解析行为。

</frozen-after-approval>

## Implementation Notes

- 复现确认：冻结程序的 `main.py` 位于发布目录 `_internal`，旧回退路径因此解析为发布根目录下不存在的 `rc/assets/chat_title_rules.json`；首次问题生成本地标题时异常向上传播并阻止发送。
- 决策：冻结运行时只读取 PyInstaller `_MEIPASS/assets/chat_title_rules.json`，源码运行继续查找兄弟 `rc` 仓库；标准 `zgwd.spec` 在构建时强制校验并打包该共享文件。
- 防御性处理：标题规则属于非关键增强；缺失、不可读、非法 JSON 或非对象 JSON 时使用结构完整的空规则，不能再阻断消息发送。
- 盲审修正：将规则快照纳入 mc 仓库，消除打包对兄弟 rc 仓库的硬依赖；补充非 UTF-8、字段类型异常和失败不缓存的边界，并修正机器相关的旧路径断言。
- 修改：`main.py`、`assets/chat_title_rules.json`、两个 PyInstaller spec、`tests/test_main_unit.py`、`tests/test_packaging_specs.py`。
- 验证：相关单元与打包契约测试 19 项通过；`zgwd.spec` 真实构建成功，暂存包内规则与仓库快照 SHA-256 一致。

## Review Triage Log

- `medium`：非 UTF-8 文件会抛出 `UnicodeDecodeError`；已捕获 `UnicodeError` 并加入非法字节回归。
- `medium`：规则字段为 `null` 或非数组时会抛出 `TypeError`；已逐字段验证数组类型并降级为空数组。
- `medium`：瞬时读取失败会永久缓存空规则；已限定只有合法文档才写入默认缓存，并验证文件恢复后可重试。
- `medium`：两个 spec 对兄弟 rc 仓库形成硬构建依赖；已将规则快照纳入 mc 仓库并改为包内资源。
- `low`：源码路径测试硬编码旧机器目录；已按当前仓库位置计算兄弟 rc 路径。
- `low`：打包测试仅宽泛搜索文本；已断言精确数据文件映射，并以真实 PyInstaller 构建及包内文件哈希补足验证。
