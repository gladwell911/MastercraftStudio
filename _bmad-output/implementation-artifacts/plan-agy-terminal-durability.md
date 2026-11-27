---
title: 'AGY 终态写入后发布'
type: 'bugfix'
ticket: ''
created: '2026-11-27'
status: 'built'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: [quick]
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
baseline_revision: '2049ad87d442d08cc651fd78841e227bc8902a11'
---

<frozen-after-approval reason="human-owned intent">

## Intent

MC receives AGY's original completion or cancellation before the ordinary save timer flushes. Persist that exact accepted turn in SQLite before publishing its terminal state through wx or shared terminal watch. Store failure retries the write and reports a save error if exhaustion occurs. Unknown provider results still follow the existing recovery path.

</frozen-after-approval>

## Implementation Notes

- AGY 在现有 worker 使用独立 SQLite 事务登记已确认的完成或取消。事务核对原生归属、清空代次、删除标记和请求 token。缺行仅补入精确的下一位置。
- 事务登记完成正文及唯一可读身份，同事务内进行完成通知对账。较晚的同 token pending 保存保持已登记的终态。
- SQLite 写失败仅重试存储。耗尽后显示保存未确认。持久归属拒绝改为采用已存聊天和历史，且在 wx 接手之前暂停该 owner 的普通及关闭快照写入。
- 回归在真实 wx 事件循环和可控 ACP 子进程使用隔离数据库，覆盖已接收终态。AGY 回放中未提供的终态和未知回合部分正文另属后续阶段。

## Plan Change Log

## Review Triage Log

- 2026-11-27 快速复审 3 项，均修复。运行中的 AGY 终态事务将普通 wx 保存计时器延后，真实 SQLite 写锁持有超过 300ms 后 wx 仍可响应。
- 正常关闭等待已获准的 AGY 终态事务。测试在关闭生产者栅栏完成后仍暂不写退出快照，释放事务后重开 SQLite 可读完整终态。
- 拒绝旧 SQLite 归属不会走普通失败处理。独立替换请求和原生会话后，保持新记录且不创建旧终答。

## Verification

Run temporary ACP and production wx integration cases for immediate completion, persisted pending, cancellation, and clean close. Hold an independent SQLite write lock before publishing to verify responsiveness, then release and reopen the database. Check revision/session/request identity, stale pending overlap, and single completion notification.


结果

- 修复前从共享终端观察到 done 后重开 SQLite，分别复现缺行和 pending。
- 修复后 17 项专门回归通过。包括正常完成和明确取消、单一完成通知、不同来源无误发、不重发 prompt、落盘早于 wx、写锁与关闭重叠及归属更换。
- 宽定向 126 项通过、10 项历史与存储基线断言失败。Windows 测试子进程以 UTF-8 编码运行。
