---
title: '保留 AGY 未知回合的部分回答'
type: 'bugfix'
ticket: ''
created: '2026-11-27'
status: 'built'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
baseline_revision: '8be323d9c6b103f2da1f72d3340031ad82e2d633'
---

<frozen-after-approval reason="human-owned intent">

## Intent

AGY 连接在返回明确结束结果前中断时，保存 MC 已经收到的部分回答，并在重开原聊天后提供原生键盘查看入口。回合继续显示结果未知，部分文本不生成终答、已读序号或完成通知，也不进入续问上下文或普通回答的文件提取。现有实时显示和已经确认的正常完成、取消继续按原路径处理。

</frozen-after-approval>

## Implementation Notes

采用单次窄改动。使用 SQLite 现有回合 JSON 与正常退出屏障，在原生答案列表加入独立显示条目，针对 AGY 未结束的两处持久执行事件加门槛。

AGY 只有在原聊天与回合仍有效、收到部分正文且没有明确终态时，才将原文写入现有 SQLite 回合 JSON 的可选字段。明确终态由前一轮的持久化机制处理。已获得充分原生证据而结算的回合会清除旧部分字段。

部分回答在原生列表、分页、焦点和查看器中有独立身份。查看器保留原文空白，既不接收普通回答的续聊载荷，也不生成阅读目标。正常保存和关闭快照阻止未结束的 AGY 流式输出生成持久执行终答事实。

## Plan Change Log

## Review Triage Log

- 中等，已修。共享查看器原先会删掉实际正文开头的空行。部分回答查看和复制保留这些空行，普通查看继续使用原显示规则。
- 中等，已修。键盘重开历史时，较早的完整回答原先会盖过最新部分回答的焦点。重开时已验证定位最新可查看回答，并且不会确认较早终答为已读。

## Verification

以真实 wx 事件循环和可控 ACP 子进程向同一回合分段输出后断开，先触发待完成保存，再正常退出并由新窗口加载同一临时数据库。核对部分原文、未知标记、原聊天归属及 Enter 入口。运行 AGY 历史、终态持久化、通知和答案窗口定向回归。


固定基线 8be323d 的分段 ACP 中断复现触发缺少 agy_partial_answer_md 的断言。修复后两块原文经正常关闭和新建窗体从同一 SQLite 回读。原生双击事件、Enter 处理器、文本查看器与复制原文均通过。另一聊天、空流、原生明确完成与取消及写失败负控通过。开启 V2 的正常保存和关闭路径只生成提问执行事实。隔离远程历史接口无部分字段或正文，通知和可读序号均未生成。

在候选根目录以 PYTHONIOENCODING=utf-8 串行运行：

    D:/code/sj/mc/.venv/Scripts/python.exe -m pytest -q -p no:faulthandler tests/test_agy_native_history.py tests/test_agy_terminal_durability.py tests/test_terminal_reply_notifications.py tests/test_answer_presentation_ui_automation.py tests/test_answer_read_open.py tests/test_file_extraction_ui_unit.py

68 项通过。本轮验证覆盖轮次中断后的正常关闭与重开。MC 进程在写入前被硬终止、真实账号的实体读屏及锁屏不在该验证范围。
