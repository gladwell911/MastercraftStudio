# 当前交接

截至 2026-10-01。五 Epic 已合入 `main`，本轮深审后的详情投影和聊天信息刷新修复也在当前分支；手机对应 `D:/code/sj/rc` 的 `master`。本阶段收尾按配置的 GitHub 上游提交并正常推送，实际同步状态以 `git status -sb` 为准；未重新打包或发布。此前合并代码基线为 MC `8bf197f5`、RC `4f20ec54`。

## 深审修复与接续

monkey 完成五 Epic 一轮深审，director 修复 7 项 MC 产品问题并补 1 项相关 IPC 检查，engineer 独立验证通过。回答详情保留链接目标、空行、嵌套列表层级和表格列；非法列表起始序号默认 1，转换失败会销毁窗口。换行边界不再重复拼接全文。Codex/Kimi 的慢上下文读取结束后，仅对仍有效的同一窗口/owner 补发一次完整刷新，成功和失败均覆盖。

最终定向验证 149 项通过：详情投影/原生 UI 25 项、信息 UI 67 项、client/process 57 项；engineer 在最终树独立重跑 27 项通过。完整转换的 8000 段输入从约 0.716 秒降到 0.384 秒；边界解析单独测量从约 0.365 秒降到 0.026 秒，不能据此声称 Markdown 依赖整体线性。详情 GUI 的既有 COM 日志保留，断言通过且 exit 0。

本仓库 cwd，使用 `.venv/Scripts/python.exe -m pytest`：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_answer_detail_projection_regression.py tests/test_answer_presentation_ui_automation.py -q
.venv/Scripts/python.exe -m pytest tests/test_chat_information_ui_automation.py -q
.venv/Scripts/python.exe -m pytest tests/test_codex_worker_client.py tests/test_codex_worker_process.py -q
```

审查、修复、独立验证报告保存在本仓库 `_bmad-output/implementation-artifacts/` 下的 `review-five-epics-deep-20261001.md`、`fix-five-epics-results-20261001.md`、`verify-five-epics-fixes-engineer-20261001.md`。原始独立日志仍在工作区同名目录，不入库。四审查视角中后两视角复用线程，上下文独立性受限。

下一步仅按需要接续发布前验证；3 项待证实/既有候选见 `deferred-work.md`，不视为本轮未修复的确认缺陷。ACK 前 metadata 的人工注入没有真实写入路径，不应据此放宽 generation 守卫。本轮只证明受影响 MC 行为，未重跑全部五 Epic、生产 Live 或实体机。

## 此前五 Epic 核心 QA（2026-10-01）

五 Epic 核心桌面/模拟器范围通过，由 engineer 独立核对；不是完整产品套件。测试未修改产品代码，不等于生产发布验证。

| 范围 | 最终结果 |
|---|---|
| Epic1 | 原生快捷键/真实10秒timer 10项，归属与用量UI 55项通过 |
| Epic3/5 | 原生恢复3项、继续与排序28项通过，原严格前台/焦点/键盘断言保留 |
| Epic4 | 桌面展示10项；Android累计299/300秒、未知、跨日、实际“更多”分页3场景通过 |
| 跨端/通知 | Local双provider、私有NATS通知实际点击通过；独立安全锁屏可视通过 |

通知传输/点击与锁屏 production-builder fixture 是独立场景；实际锁屏为两个准确标题各两条和“新消息”，无私密正文。完整命令、初失败、截图、清理证据在 `D:/code/sj/_bmad-output/implementation-artifacts/tests/test-summary.md`，非 Git 工作区产物不随仓库提交。

仍未验证生产 Live、真实模型服务、实体机 TalkBack/震动/后台全部行为、重新打包和双机；旧宽套件 voice/Kimi 精确失败仍按历史计划复核，COM日志限制保留。模拟器 QA PIN/通知清除，隐私设置恢复1/1，showing=false/secure=false；owned进程与adb reverse已清，个人数据库未改变。

## 复现桌面测试

本仓库 cwd，使用已有 Python3.11 `D:/code/sj/mc/.venv/Scripts/python.exe`：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'tests')
python -m pytest -p owned_window_qa tests/test_model_session_recovery_ui_automation.py tests/test_desktop_navigation_ui_automation.py -q -s
python -m pytest tests/test_epic1_native_e2e.py -q -s
python -m pytest tests/test_chat_information_ui_automation.py -q
python -m pytest tests/test_answer_presentation_ui_automation.py -q
```

helper `tests/owned_window_qa.py` 为已验证源文件的逐字节副本，仅准备测试窗口前台，不替换原键盘或断言。wx GUI 串行；测试隔离 app/notes 数据。不要只反复 Raise 或绕过前台检查。

## 后续与有效背景

- Epic1 聊天信息、Epic2 通知事实、Epic3 恢复、Epic4 回答展示、Epic5 继续与排序已有实现；详细历史基线和 deferred 项见工作区相应 `plan-epic*`/`qa-*`/`review-*`。已经合并，尚未发布。
- 源码笔记为 `D:/code/note/notes.db`；打包版要求个人 OneDrive 下 `code/data/sj/notes.db` 已存在并通过完整性/表结构校验，常用命令同目录 `common_commands.json`。旧安装仍写源库，2026-09-29 云端备份仅是快照。切包前从最新源库一致性备份并安全替换或合并；其他电脑独有笔记先导出，一次只运行一台 MC，换机先退出并等同步。
- 发布前按实际范围接续 Live、真实模型、实体设备和新包双机验证，不重复已有效定向测试。
- Git 清理（2026-10-01）：全部已合并功能分支已删除，本仓库仅保留 main；测试工作树切为 detached HEAD，文件和构建数据保留。RC 旧备份历史已完整归档为 `D:/code/sj/.sync-backups/rc-pre-sync-20260906-add9e1d-20261001.bundle` 并通过 git bundle verify，原备份分支已删除。RC 主树 ASR 文件字节保留，不纳入本轮提交。

[历史快照](archive/entry-context-2026-09-29/handoff.md)只证明当时版本。
