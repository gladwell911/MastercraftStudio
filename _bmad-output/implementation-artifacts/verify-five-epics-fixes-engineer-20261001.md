# 五 Epic 修复独立验证 — 2026-10-01

结论：R1-R8 定向验证通过，未发现需要继续修复的问题。engineer 只读产品/测试代码并运行检查，无提交、推送或打包。

最终 MC main.py SHA256：913C8B5B6DBE6AA70002F44AA24A6D60F9D416B54DD6170F33A1FF805325C2EA；独立检查前后相同。已读取项目 AGENTS 与 handoff/experience/reflection，并逐项核对审查清单、实际 diff、测试断言和隔离 fixture。

## 独立运行证据

cwd D:/code/sj/mc，.venv/Scripts/python.exe -m pytest：

`tests/test_answer_detail_projection_regression.py tests/test_codex_worker_process.py::test_context_only_information_does_not_query_account_or_quota tests/test_chat_information_ui_automation.py::test_full_refresh_queued_behind_context_request -q`

27 passed in 5.07s，exit 0。原始输出：`engineer-five-epics-targeted-20261001.log`。git diff --check exit 0（仅已有LF/CRLF提示）。wx串行，frame fixture隔离app/notes路径并清理timer/窗口。

- R1-R5：14投影回归精确比对非法/无值/合法起始序号、非冗余URL、连续br/段落、HTML与真实Markdown嵌套列表、表格行列、代码块字面内容；独立检查详情转换位于try/finally，已有原生窗口销毁及canonical/scratch断言保留。
- R6：边界回归禁止读取累计text()；独立AST提取HEAD与最终生产解析类，HTML `<p>paragraph content</p>` ×2000/4000/8000，3次中位数baseline 0.0282/0.0980/0.3645s，fixed 0.0069/0.0133/0.0263s；逐次确认内容数量。边界解析呈线性，不能据此声称Markdown依赖整体线性。
- R7：12参数场景实际运行，Codex/Kimi各覆盖成功/失败与正常/关闭/owner变化；重复完整timer请求只补发一次，关闭/owner变化不补旧查询。测试核对Codex context_only True→False，以及Kimi status→status+snapshot的精确次数，未用弱断言掩盖失败。
- R8：真实CodexWorkerClient写JSON到stdin，再由runtime.handle_message消费；精确断言True标志、上下文结果及没有account/rate_limits调用。

## 复用证据与范围

director最终25投影/详情原生UI、67信息UI、57client/process，共149项通过的工具输出由根代理保留；对应断言与范围已独立核对。最后微调仅详情排版空白和Markdown嵌套，25项已在最终代码复验，信息与IPC代码之后未变，故不重复全GUI。director报告是结果索引，不能替代原始工具输出。

本轮只验证实际受影响MC范围；不代表全产品、真实模型服务、生产Live或打包通过。详情UI历史COM日志且exit 0的限制保留。deferred/rejected不扩展；RC既有ASR未修改。
