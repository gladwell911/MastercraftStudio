# 五 Epic 深审修复结果

director 完成 R1-R8，仅修改 MC；deferred/rejected、RC既有ASR保留。不提交、推送、打包。bmad-build render一次，oneshot/review=none，基线67f7c6acb9e2b11b7e0240cffcb0222789332af0。

## 改动

- `mc/main.py`：非法/空ol start默认1；详情转换纳入销毁finally；保留非冗余链接、连续br、列表标记/缩进、每行表格列；换行边界只读局部尾部状态；过滤标签间排版空白而保留pre字面内容。
- Codex/Kimi在慢context读取期间记下一次完整刷新，同dialog/identity完成后补发；成功/失败均覆盖，关闭/换owner不补旧查询。
- `test_codex_worker_process.py`：真实CodexWorkerClient发送context_only=True的JSON经worker消费，确认没有account/rate_limits调用。

## 定向验证

cwd `D:/code/sj/mc`，Python `./.venv/Scripts/python.exe`。以下结果来自本次工具运行输出；未重跑另存原始日志。wx串行，conftest将app/notes路径隔离至tmp_path，真实timer在fixture清理。

|命令（均 python -m pytest）|结果|
|---|---|
|`tests/test_answer_detail_projection_regression.py tests/test_answer_presentation_ui_automation.py -q`|最终25 passed，14.69s（14投影 + 11原生UI）|
|`tests/test_chat_information_ui_automation.py -q`|67 passed，25.31s（含新增12场景）；之后该范围未改|
|`tests/test_codex_worker_client.py tests/test_codex_worker_process.py -q`|最终57 passed，0.17s|
|`git diff --check`|exit 0|

共149项。详情GUI输出已知Windows COM 0x8001010d日志，测试进程exit 0且全部断言通过；没有削弱断言。

## 同输入性能比较

AST提取HEAD版_ParagraphStripper/answer_detail_plain_text为baseline，与当前版共用相同依赖；输入`'paragraph content\n\n' * count`，每版3次中位数，包含Markdown转换。

|段数/字节|baseline秒|fixed秒|
|---|---|---|
|2000/38000|0.0782|0.0552|
|4000/76000|0.2204|0.1392|
|8000/152000|0.7163|0.3842|

8000段整条转换降低约46%；边界测试禁止text()读取累积全文，避免原平方拼接。最后仅过滤HTML排版空白并补真实Markdown嵌套回归；engineer可独立测最终树，不声称Markdown依赖本身线性。

仅核心受影响范围，未跑全产品、生产Live、打包。engineer 已在最终树独立验证 27 项通过，见同目录 `verify-five-epics-fixes-engineer-20261001.md`。本报告中的“不提交、推送、打包”指 director 修复阶段；后续 neat-freak 收尾提交代码及文档，未打包。
