---
title: '更新电脑端默认打包位置'
type: 'chore'
created: '2026-09-09'
status: 'done'
route: 'one-shot'
---

# 更新电脑端默认打包位置

## Intent

**Problem:** 打包脚本默认输出到不存在的 `C:\code\cx`，与电脑端程序实际运行的 `D:\code\cx\mc` 不一致。

**Approach:** 将默认产物父目录改为 `D:\code\cx`，并同步说明最终目录以及 `-DistPath` 的父目录语义。

## Suggested Review Order

- 默认参数直接指向当前电脑端发布根目录。
  [`package_mc.ps1:2`](../../package_mc.ps1#L2)

- 文档明确最终目录与父目录参数的关系。
  [`README.txt:70`](../../README.txt#L70)
