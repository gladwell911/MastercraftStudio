---
title: '支持已验证的 AGY 1.5.18 删除确认'
type: 'bugfix'
ticket: ''
created: '2026-11-26'
status: 'built'
route: 'oneshot'
route_source: 'auto'
baseline_revision: '0338c1f39ff286075076cc3df498786c02498f01'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 本机 AGY 从 1.5.17 自动升级至 1.5.18。旧版本条件拒绝 1.5.18 的原生删除结果，即使该版本在合成会话中已通过真实 F4 删除及独立清单、加载缺失证明。
**Approach:** 允许已验证的 1.5.18 使用既有删除确认。保持原始账号、来源、存储、清单与明确加载缺失的检查，其他版本仍需验证。

</frozen-after-approval>

## Implementation Notes

同一临时项目的两个独立会话用于区分目标和对照。真实 1.5.18 F2 改名、F4 取消，以及进程重开后的清单和指定 ID 加载结果均已核对。

## Plan Change Log

## Review Triage Log

- Low，补强了未知版本的验证。通过实际远程确认入口检查 409、零原生请求及聊天仍存在。旧版本与新版本走相同入口并核对完整的只读证明。

## Verification

- 在原版本条件下，具备缺失证据的 1.5.18 用例失败。修复后验证 1.5.17 和 1.5.18 均成功，未验证的版本仍保留会话。
- 用固定版本真实 AGY 对已测试删除的合成目标验证 MC 确认函数。
- 真实 1.5.18 在新的 ACP 进程完成目标清单缺失、同 ID 加载缺失与对照会话可读。修复前 MC 拒绝，修复后直接确认通过。定向回归 18 项通过。
