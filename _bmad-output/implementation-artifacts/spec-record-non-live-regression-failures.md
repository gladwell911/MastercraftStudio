---
title: '记录非实时回归失败基线'
type: 'chore'
created: '2026-09-06'
status: 'done'
route: 'one-shot'
baseline_commit: '9c28e5acf93b26c3b7b6e851a5071a514fb50659'
---

# 记录非实时回归失败基线

## Intent

**Problem:** Kimi 修复的完整非实时验证发现 120 个跨领域失败，后续维护者缺少可复现命令、运行环境和逐项测试索引。

**Approach:** 在项目文档中冻结一次验证基线，按失败领域列出节点、已知约束、环境信息和后续复现方法，不改动产品代码或测试契约。

## Suggested Review Order

**回归基线与证据**

- 先核对命令、环境和缓存边界。
  [non-live-regression-baseline-2026-09-06.md:1](../../docs/non-live-regression-baseline-2026-09-06.md#L1)

- 再按领域检查全部失败节点。
  [non-live-regression-baseline-2026-09-06.md:48](../../docs/non-live-regression-baseline-2026-09-06.md#L48)
