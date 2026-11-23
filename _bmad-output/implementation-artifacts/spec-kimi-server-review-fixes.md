---
title: '修复 Kimi Server API 审查发现'
type: 'bugfix'
created: '2026-11-23'
status: 'done'
baseline_revision: '70759dc04c9603d366f82306174113127adad834'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
deferred: []
---
<intent-contract>

## 意图

修复正式审查确认的六项问题。保留本机 Kimi 2.6.3、现有 UI 契约及旧版兼容路径。不得替换日常安装或写入真实会话。

</intent-contract>

## 代码与验收

- kimi_server_client.py：审批提交结果不明后，用 2.6.3 的权威 pending 查询区分可重试、已结束和未知，网络异常仍为未知；成功响应必须核对交互 ID 和实际 outcome，与请求不一致时不得显示成功。
- kimi_server_api.py 与相关执行过程逻辑：按实际 2.6.3 帧的 item_id 和 set 合并 item.updated，处理 item.removed，并撤回已经发布的同一归属执行内容，迟到 delta 不得复活。保留隐私过滤。
- kimi_server_api.py：终态 REST 查证离开唯一 reader，使用有界、可清理的后台工作或既有补查。慢查证期间同 socket 仍可处理心跳及其他会话；迟到结果按 generation/归属丢弃，不用虚假 transport_recovered 通知掩盖补查。
- kimi_server_v2.py：允许只有非 agent turn 但原始 ordinal 游标有进展的页，保留无进展终止和有界分页。
- tests/test_codex_client_unit.py：自动化核对真实子进程环境只注入三项代理、独立机器覆盖和缺配置回退。
- 为前五项新增足以复现的回归，保持旧协议测试通过。详见 [审查清单](spec-local-kimi-cli.md#review-findings)。延期项留待独立证据。

## Verification

- .venv/Scripts/python.exe -m pytest -q tests/test_kimi_server_client_unit.py tests/test_codex_client_unit.py tests/test_packaging_specs.py
- 执行修复涉及的 Kimi 运行时、执行过程和审批定向回归；记录已知基线失败。独立验证由 engineer 完成。

## Spec Change Log

## Review Triage Log

### 2026-11-23 — 复审

- 盲审 10 项。修复新增 opener 引起的撤回、私有 opener 正文、快照替换遗漏、旧连接审批证据复用、后台异常丢失、游标循环、暂存目录扫描缺口。嵌套部分更新缺少真实合约，按字段原子应用。用量投影要求完整输入与输出以支持精确汇总，部分计数延期到单独验证。旧状态交由收尾文档同步。
- 边界审查 6 项。审批重订阅、归属撤回、快照和隐藏字段、未发送的旧式订阅请求已处理。早于 opener 的工具行缺真实顺序证据，且执行页使用现有叙述筛选，暂不新增重放。
- 验证差距 1 项。补了 transcript_recheck 进入真实 wx frame 后恢复并写入 SQLite 的回归。
- 意图审查核对六项修复的测试层级。用真实 2.6.3 重订阅确认审批精确 ID，成功批准和拒绝回包均只有 ended_at 和 outcome。旧历史跨版本、降级、台式机和读屏仍待验证。

## Auto Run Result
六项正式审查补丁及复审确认的可修复问题已实施。定向测试和隔离 wx 真云聊天与审批通过，标准 BuildOnly 退出 0。用实际打包后的 mc.exe 选中 Kimi Code、发送并收到了精确的 FROZEN_KIMI_REVIEW_OK_842，同时确认已完成且持久化。日常安装未替换。